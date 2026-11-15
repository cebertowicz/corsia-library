import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("publisher", ROOT / "review_gate_publish.py")
publisher = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = publisher
spec.loader.exec_module(publisher)


class FakeClient:
    def __init__(self, *, head="abcdef1234567", prior=(), refuse=(), raises=()):
        self.head, self.prior, self.refuse, self.raises = head, list(prior), set(refuse), set(raises)
        self.calls = []
    def _call(self, name, *args):
        self.calls.append((name, *args))
        if name in self.raises: raise RuntimeError(name)
    def current_head(self, repo, pr): return self.head
    def list_reviewer_findings(self, repo, pr): self._call("list", repo, pr); return self.prior
    def upsert_summary(self, *args): self._call("summary", *args)
    def post_finding(self, *args): self._call("finding", *args); return "finding" not in self.refuse
    def cited_line_report(self, repo, head, prior):
        self._call("report", repo, head, prior.thread_id)
        return "Cited lines unchanged since finding commit (`" + "a" * 8 + "` → `" + "b" * 8 + "`); not verified fixed."
    def reply(self, *args): self._call("reply", *args)
    def set_status(self, *args): self._call("status", *args)
    def set_thread_resolved(self, repo, thread_id, resolved): self._call("resolve", repo, thread_id, resolved); return "resolve" not in self.refuse


def finding(fp="f1", severity="must_fix", text="Current text.", **extra):
    value = {"severity": severity, "category": "security/path traversal", "text": text, "fingerprint": fp, "path": "upload.py", "line": 18}
    value.update(extra); return value


def envelope(verdict="findings", findings=None, **payload_extra):
    payload = {"verdict": verdict, "findings": [] if findings is None and verdict == "pass" else (findings if findings is not None else [finding()])}
    payload.update(payload_extra)
    return {"source": "outside", "repo": "example-org/example-repo", "pr": 12, "head_sha": "abcdef1234567", "run_id": "run-12345678", "workflow": "pr-reviewer", "workflow_version": "v1", "kind": "qa.review", "valid": True, "payload": payload, "model": "example-model"}


def prior(fp, severity="must_fix", text="Current text.", **state):
    marker = {"fingerprint": fp, "severity": severity, "category": "security/path traversal", "head_sha": "oldhead1", "text_hash": publisher._hash(text), "model": "example-model"}
    return {"thread_id": state.pop("thread_id", "thread-" + fp), "body": "old body\n<!-- corsia-review: " + __import__("json").dumps(marker) + " -->", "resolved": state.pop("resolved", False), "outdated": state.pop("outdated", False), **state}


class PublishTests(unittest.TestCase):
    settings = publisher.Settings("example-model", show_model_in_output=True, sme="maintainer", rules={"EAS14": {"text": "A path built from request input is resolved and checked against its root before use.", "file": "engineering-architecture.md"}})

    def call_names(self, client, name): return [call for call in client.calls if call[0] == name]

    def test_mockups_1_to_4_and_r12(self):
        self.assertEqual(publisher.render_summary(envelope("pass"), self.settings, {s: {k: 0 for k in ("Open now", "New", "Still open", "Fixed", "Dropped")} for s in publisher.SEVERITIES}, first_review=True), "### Review: no issues found\n\nAutomated review of commit abcdef1 by example-model.")
        client = FakeClient(); publisher.publish(envelope("findings", [finding("s", "should_fix"), finding("o", "optional")]), self.settings, client)
        summary = self.call_names(client, "summary")[0][-1]
        self.assertIn("### Review: nothing blocking", summary); self.assertIn(publisher.BEFORE_CHANGE, summary)
        client = FakeClient(); publisher.publish(envelope("stop", [finding()], shared_cause="retry state is duplicated."), self.settings, client)
        self.assertIn("### Review: this needs a different approach", self.call_names(client, "summary")[0][-1])
        body = publisher.render_finding({**finding(rule_id="EAS14"), "repo": "example-org/example-repo", "head_sha": "abcdef1234567", "base_sha": "base123"}, self.settings)
        self.assertIn("**Must fix** · Security: path traversal", body); self.assertIn("#eas14", body); self.assertIn('"head_sha": "abcdef1234567"', body)
        # A finding must carry a marker the platform's has_reviewer_marker sees (a line opening with
        # <!-- corsia-review-fp:), or evaluate takes our own post for the author's reply and re-reviews.
        self.assertTrue(any(line.startswith("<!-- corsia-review-fp:") for line in body.split("\n")))
        self.assertIsNotNone(publisher.parse_prior({"thread_id": "t", "body": body, "resolved": False, "outdated": False}))  # still read back as a prior

    def test_mockups_5_and_6(self):
        client = FakeClient(prior=[prior("gone", thread_id="t")]); publisher.publish(envelope(replies=[{"thread_id": "t", "fingerprint": "gone", "outcome": "still_open", "evidence": "archive caller remains"}], sme_dispute={"thread_id": "t", "author": "safe", "review": "unsafe", "severity": "must_fix"}), self.settings, client)
        self.assertTrue(any(call[-1].startswith("Still open:") for call in self.call_names(client, "reply")))
        self.assertTrue(any("@maintainer, can you weigh in?" in call[-1] for call in self.call_names(client, "reply")))
        # Every reply publish posts (still open, dropped, SME ping, resolve) opens a line with a marker the
        # platform's has_reviewer_marker sees, so it is not mistaken for a new reply.
        client = FakeClient(prior=[prior("a", thread_id="ta"), prior("b", thread_id="tb"), prior("c", thread_id="tc")])
        publisher.publish(envelope(findings=[], verdict="pass", replies=[{"thread_id": "ta", "fingerprint": "a", "outcome": "dropped", "evidence": "fixed"}, {"thread_id": "tb", "fingerprint": "b", "outcome": "still_open", "evidence": "still there"}],
                                   sme_dispute={"thread_id": "tb", "author": "safe", "review": "unsafe", "severity": "must_fix"}), self.settings, client)
        bodies = [call[-1] for call in self.call_names(client, "reply")]
        self.assertEqual(len(bodies), 4)
        for body in bodies:
            self.assertTrue(any(line.startswith(("<!-- corsia-review-fp:", publisher.RESOLVED_REPLY_MARKER, publisher.REVIEW_SUMMARY_MARKER)) for line in body.split("\n")), body)
        client = FakeClient(); publisher.publish(envelope(product_decision="This change changes behavior. No approved issue covers it, so the product owner decides."), self.settings, client)
        self.assertIn("Needs a product decision · example-model", str(self.call_names(client, "status")))

    def test_every_status_line_and_show_model_toggle(self):
        # Each status state has a distinct message.
        expected = {"safety_check": ("pending", "Checking the change before review"), "held": ("pending", "Held for a maintainer"),
                    "waiting_checks": ("pending", "Waiting for checks · review starts when they pass"), "maintainer_checks": ("pending", "Waiting for a maintainer to run the checks"),
                    "requested": ("pending", "Review requested"), "skipped": ("success", "Review skipped by a maintainer"),
                    "error": ("error", "Review couldn't finish · retrying · ref 8cbd2966")}
        for state, line in expected.items():
            self.assertEqual(publisher.status_for_state(state, self.settings, run_id="8cbd2966-run"), line)
        # Product Experience's approved line, with the PR's own base branch (11-05).
        self.assertEqual(publisher.status_for_state("conflicts", self.settings, base_ref="release/0.5"),
                         ("pending", "Waiting for conflicts with release/0.5 to be resolved"))
        verdicts = [(envelope("pass")["payload"], ("success", "No issues found · example-model")),
                    ({"verdict": "findings", "findings": [finding("a", "should_fix"), finding("b", "should_fix")]}, ("success", "Nothing blocking, 2 should fix · example-model")),
                    ({"verdict": "findings", "findings": [finding("a"), finding("b"), finding("c", "should_fix")]}, ("failure", "2 must fix, 1 should fix · example-model")),
                    ({"verdict": "stop", "findings": [finding("a"), finding("b"), finding("c")]}, ("failure", "Different approach needed, 3 must fix · example-model")),
                    ({"verdict": "findings", "findings": [finding("a", "should_fix")], "product_decision": "x"}, ("failure", "Needs a product decision · example-model"))]
        for payload, line in verdicts:
            self.assertEqual(publisher.status_line(payload, self.settings), line)
        pass_with_decision = {"verdict": "pass", "findings": [], "product_decision": "x"}
        self.assertEqual(publisher.status_line(pass_with_decision, self.settings), ("failure", "Needs a product decision · example-model"))
        self.assertEqual(publisher._heading(pass_with_decision, publisher._counts([])), "### Review: needs a product decision before this can merge")
        hidden = publisher.Settings("example-model")  # The model is hidden by default.
        self.assertNotIn("example-model", publisher.status_line(envelope()["payload"], hidden)[1])
        self.assertNotIn("example-model", publisher.render_summary(envelope("pass"), hidden, {s: {k: 0 for k in ("Open now", "New", "Still open", "Fixed", "Dropped")} for s in publisher.SEVERITIES}, first_review=True))
        self.assertIn('"model": "example-model"', publisher.render_finding({**finding(), "head_sha": "abcdef1234567"}, hidden))

    def test_r6_arithmetic_columns_and_dropped_not_fixed(self):
        rows = publisher._rows([finding("open"), finding("new", "should_fix")], {"open": publisher.parse_prior(prior("open")), "drop": publisher.parse_prior(prior("drop"))}, [{"fingerprint": "drop", "outcome": "dropped"}])
        for severity in publisher.SEVERITIES: self.assertEqual(rows[severity]["Open now"], rows[severity]["New"] + rows[severity]["Still open"])
        self.assertEqual(rows["must_fix"]["Dropped"], 1); self.assertEqual(rows["must_fix"]["Fixed"], 0)
        text = publisher.render_tally(rows); self.assertIn("Dropped", text); self.assertIn("–", text)

    # Exercise the finding lifecycle.
    def test_bookkeeping_reraise_suppression_replies_only_changed_text(self):
        client = FakeClient(prior=[prior("same", text="old")]); publisher.publish(envelope(findings=[finding("same", text="new")]), self.settings, client)
        self.assertEqual(len(self.call_names(client, "reply")), 1); self.assertEqual(len(self.call_names(client, "finding")), 0)

    def test_a_resolved_prior_thread_is_reopened_and_replied_to_not_reposted(self):
        client = FakeClient(prior=[prior("same", resolved=True)]); publisher.publish(envelope(findings=[finding("same")]), self.settings, client)
        self.assertEqual([call[0] for call in client.calls if call[0] in {"resolve", "reply", "finding"}], ["resolve", "reply"])
        self.assertEqual(self.call_names(client, "resolve")[0][1:], ("example-org/example-repo", "thread-same", False))
        self.assertIn("Current text.", self.call_names(client, "reply")[0][-1])

    def test_a_refused_reopen_falls_back_to_a_fresh_comment(self):
        client = FakeClient(prior=[prior("same", resolved=True)], refuse={"resolve"}); publisher.publish(envelope(findings=[finding("same")]), self.settings, client)
        self.assertEqual(self.call_names(client, "reply"), []); self.assertEqual(len(self.call_names(client, "finding")), 1)

    def test_an_outdated_resolved_thread_still_gets_a_fresh_comment(self):
        client = FakeClient(prior=[prior("same", resolved=True, outdated=True)]); publisher.publish(envelope(findings=[finding("same")]), self.settings, client)
        self.assertEqual(self.call_names(client, "resolve"), []); self.assertEqual(len(self.call_names(client, "finding")), 1)

    def test_an_absent_finding_gets_its_reply_then_its_thread_resolved(self):
        client = FakeClient(prior=[prior("gone")])
        publisher.publish(envelope("pass", [], replies=[{"thread_id": "thread-gone", "fingerprint": "gone", "outcome": "dropped", "evidence": "The path is checked now."}]), self.settings, client)
        names = [call[0] for call in client.calls]
        self.assertLess(names.index("reply"), names.index("resolve"))
        self.assertEqual([call[1:] for call in self.call_names(client, "resolve")], [("example-org/example-repo", "thread-gone", True)])
        self.assertIn("Finding dropped: The path is checked now.", self.call_names(client, "reply")[0][-1])

    def test_a_reply_naming_an_unknown_id_reaches_its_thread_by_fingerprint(self):
        client = FakeClient(prior=[prior("gone")])
        result = publisher.publish(envelope("pass", [], replies=[{"thread_id": "401", "fingerprint": "gone", "outcome": "dropped", "evidence": "Fixed."}]), self.settings, client)
        self.assertTrue(result.published)
        self.assertEqual([call[2] for call in self.call_names(client, "reply")], ["thread-gone"])
        self.assertEqual(self.call_names(client, "resolve"), [("resolve", "example-org/example-repo", "thread-gone", True)])

    def test_an_sme_dispute_naming_no_thread_is_not_posted(self):
        client = FakeClient()
        result = publisher.publish(envelope(sme_dispute={"thread_id": "nope", "author": "a", "review": "r", "severity": "must_fix"}), self.settings, client)
        self.assertTrue(result.published)
        self.assertFalse(self.call_names(client, "reply"))

    def test_a_still_open_reply_leaves_the_thread_open(self):
        client = FakeClient(prior=[prior("gone")])
        publisher.publish(envelope("pass", [], replies=[{"thread_id": "thread-gone", "fingerprint": "gone", "outcome": "still_open", "evidence": "Still reachable."}]), self.settings, client)
        self.assertEqual(self.call_names(client, "resolve"), []); self.assertEqual(len(self.call_names(client, "reply")), 1)

    def test_a_prior_the_worker_said_nothing_about_is_replied_to_then_resolved(self):
        client = FakeClient(prior=[prior("gone")]); publisher.publish(envelope("pass", []), self.settings, client)
        names = [call[0] for call in client.calls]
        self.assertLess(names.index("report"), names.index("reply"))
        self.assertLess(names.index("reply"), names.index("resolve"))
        self.assertEqual(self.call_names(client, "resolve"), [("resolve", "example-org/example-repo", "thread-gone", True)])
        self.assertIn("Not raised this run. Cited lines unchanged", self.call_names(client, "reply")[0][-1])
        self.assertTrue(self.call_names(client, "reply")[0][-1].endswith(publisher.RESOLVED_REPLY_MARKER))

    def test_a_prior_the_worker_answered_is_not_replied_to_twice(self):
        client = FakeClient(prior=[prior("gone")])
        publisher.publish(envelope("pass", [], replies=[{"thread_id": "thread-gone", "fingerprint": "gone", "outcome": "dropped", "evidence": "The path is checked now."}]), self.settings, client)
        self.assertEqual(len(self.call_names(client, "reply")), 1)
        self.assertEqual(self.call_names(client, "report"), [])

    def test_a_publish_failure_names_its_reason_in_the_status_description(self):
        client = FakeClient(raises={"summary"}); result = publisher.publish(envelope(), self.settings, client)
        self.assertTrue(result.reason.startswith("publish_failure:"))
        description = self.call_names(client, "status")[0][-1]
        self.assertIn("publish_failure:RuntimeError: summary", description)
        self.assertLessEqual(len(description), publisher.STATUS_LIMIT)

    def test_bookkeeping_unresolved_prior_thread_is_still_open_not_duplicate(self):
        client = FakeClient(prior=[prior("same")]); publisher.publish(envelope(findings=[finding("same")]), self.settings, client)
        self.assertEqual(len(self.call_names(client, "finding")), 0); self.assertEqual(len(self.call_names(client, "reply")), 0)

    def test_bookkeeping_cross_run_file_category_rule_collision_publishes_both(self):
        one, two = finding("one", rule_id="EAS14"), finding("two", rule_id="EAS14")
        client = FakeClient(); publisher.publish(envelope(findings=[one, two]), self.settings, client)
        self.assertEqual(len(self.call_names(client, "finding")), 2)

    def test_bookkeeping_refused_anchor_falls_back_to_summary_text(self):
        client = FakeClient(refuse={"finding"}); publisher.publish(envelope(), self.settings, client)
        summary = self.call_names(client, "summary")[0][-1]
        self.assertIn("Findings not attached to a changed line", summary); self.assertIn("Current text.", summary)

    def test_bookkeeping_refused_line_falls_back_to_summary_text(self):
        client = FakeClient(refuse={"finding"}); publisher.publish(envelope(findings=[finding(line=999)]), self.settings, client)
        self.assertIn("Current text.", self.call_names(client, "summary")[0][-1])

    def test_bookkeeping_summary_upsert_failure_is_error_without_presence_success(self):
        client = FakeClient(raises={"summary"}); result = publisher.publish(envelope(), self.settings, client)
        self.assertFalse(result.published); self.assertEqual(result.events[0]["verdict"], "error")
        self.assertFalse(any(call[0] == "status" and call[-2] == "llm-review-present" for call in client.calls)); self.assertIn("publish_failure", self.call_names(client, "status")[0][-1])

    def test_bookkeeping_reply_failure_is_error_without_presence_success(self):
        client = FakeClient(prior=[prior("same", text="old")], raises={"reply"}); result = publisher.publish(envelope(findings=[finding("same", text="new")]), self.settings, client)
        self.assertFalse(result.published); self.assertEqual(result.events[0]["verdict"], "error"); self.assertFalse(any(call[0] == "status" and call[-2] == "llm-review-present" for call in client.calls))

    def test_newest_open_prior_wins_over_older_and_resolved(self):
        older, newer, closed = prior("same", text="old", thread_id="t1"), prior("same", text="Current text.", thread_id="t2"), prior("same", text="x", thread_id="t3", resolved=True)
        client = FakeClient(prior=[older, newer, closed]); publisher.publish(envelope(findings=[finding("same")]), self.settings, client)
        self.assertEqual(self.call_names(client, "reply"), []); self.assertEqual(self.call_names(client, "finding"), [])
        chosen = publisher.select_prior([publisher.parse_prior(closed), publisher.parse_prior(older)])
        self.assertEqual(chosen["same"].thread_id, "t1")

    def test_changed_text_reply_is_not_resent_once_listed(self):
        reply_marker = prior("same", text="new", thread_id="thread-same")
        client = FakeClient(prior=[prior("same", text="old"), reply_marker]); publisher.publish(envelope(findings=[finding("same", text="new")]), self.settings, client)
        self.assertEqual(self.call_names(client, "reply"), [])

    def test_product_decision_summary_matches_mockup_6(self):
        client = FakeClient(); publisher.publish(envelope(findings=[finding("s", "should_fix")], product_decision="This change makes uploads over 50 MB fail instead of splitting them. That changes what people can do, and no approved issue covers it, so the product owner decides."), self.settings, client)
        summary = self.call_names(client, "summary")[0][-1]
        self.assertIn("so the product owner decides. If you didn't mean to change this, undo it and push.", summary)

    def test_measure_lines_carry_declared_attributes_only(self):
        result = publisher.publish(envelope(findings=[finding("k", rule_id="EAS14")]), self.settings, FakeClient())
        lines = [__import__("json").loads(publisher.measure_line(event))["measure"] for event in result.events]
        self.assertEqual([line["event.name"] for line in lines], ["qa.review.completed", "qa.review.finding"])
        self.assertEqual(lines[1]["attributes"], {"pr": "12", "head_sha": "abcdef1234567", "finding": "k", "severity": "must_fix", "category": "security/path traversal", "rule_id": "EAS14"})
        self.assertNotIn("run_id", lines[0]["attributes"])
        skipped = publisher.publish(envelope(), self.settings, FakeClient(head="newhead1234567")).events[0]
        self.assertEqual(__import__("json").loads(publisher.measure_line(skipped))["measure"], {"event.name": "qa.review.skipped", "attributes": {"pr": "12", "head_sha": "abcdef1234567", "reason": "superseded"}})

    def test_cli_dry_run_prints_measures_and_refuses_live(self):
        import contextlib, io, json, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            settings_path, envelope_path = Path(tmp, "s.json"), Path(tmp, "e.json")
            settings_path.write_text(json.dumps({"model": "example-model", "sme": "maintainer"})); envelope_path.write_text(json.dumps(envelope()))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(publisher.main(["--settings", str(settings_path), "--envelope", str(envelope_path)]), 2)
                self.assertEqual(publisher.main(["--settings", str(settings_path), "--envelope", str(envelope_path), "--dry-run"]), 0)
            self.assertEqual([json.loads(line)["measure"]["event.name"] for line in out.getvalue().splitlines()], ["qa.review.completed", "qa.review.finding"])
            self.assertIn('"dry_run": "set_status"', err.getvalue())

    def test_bookkeeping_opposite_sign_never_claims_fixed_or_dropped_while_raised(self):
        rows = publisher._rows([finding("same")], {"same": publisher.parse_prior(prior("same"))}, [{"fingerprint": "same", "outcome": "dropped"}])
        self.assertEqual(rows["must_fix"]["Fixed"], 0); self.assertEqual(rows["must_fix"]["Dropped"], 0)

    def test_1171_textless_prior_marker_is_republished_at_current_head(self):
        old = prior("f0884e75"); old["body"] = "Not raised this run.\n<!-- corsia-review: {\"fingerprint\": \"f0884e75\", \"severity\": \"must_fix\", \"category\": \"correctness\"} -->"
        client = FakeClient(prior=[old]); publisher.publish(envelope(findings=[finding("f0884e75", text="Current text at f0884e75.")]), self.settings, client)
        self.assertIn("Current text at f0884e75.", self.call_names(client, "reply")[0][-1])

    def test_1014_should_fix_escalation_republishes_at_must_fix(self):
        client = FakeClient(prior=[prior("1014", severity="should_fix")]); publisher.publish(envelope(findings=[finding("1014", "must_fix")]), self.settings, client)
        self.assertIn("**Must fix**", self.call_names(client, "reply")[0][-1])

    def test_error_verdict_posts_only_error_status(self):
        client = FakeClient(); result = publisher.publish(envelope("error", []), self.settings, client)
        self.assertFalse(result.published); self.assertEqual(len(self.call_names(client, "summary")), 0); self.assertEqual(len(self.call_names(client, "finding")), 0)
        self.assertEqual(len(self.call_names(client, "status")), 1); self.assertIn("Review couldn't finish", self.call_names(client, "status")[0][-1])

    def test_invalid_superseded_events_and_input_not_mutated(self):
        data = finding(); original = dict(data); client = FakeClient(head="newhead1234567")
        result = publisher.publish(envelope(findings=[data]), self.settings, client)
        self.assertFalse(result.published); self.assertEqual(result.events[0]["reason"], "superseded"); self.assertEqual(data, original)
        invalid = publisher.publish({"repo": "example-org/example-repo", "pr": 12}, self.settings, FakeClient())
        self.assertFalse(invalid.published)

    def test_events_dedupe_and_known_rule_only(self):
        result = publisher.publish(envelope(findings=[finding("known", rule_id="EAS14"), finding("unknown", rule_id="EAS99")]), self.settings, FakeClient())
        events = [event for event in result.events if event["type"] == "qa.review.finding"]
        self.assertEqual([event["rule_id"] for event in events], ["EAS14", None]); self.assertEqual(events[0]["dedupe"], ["run-12345678", "known"])


if __name__ == "__main__": unittest.main()
