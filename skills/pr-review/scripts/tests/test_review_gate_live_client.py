"""LiveClient: the gate's live posts, made through ``corsia github api``, against a fake gh.

The tests assert the GitHub calls and negative criteria: an outdated still-raised finding is re-posted rather than
replied to, a thread is only resolved after its reply lands, only configured status contexts are
writable, and a rejected anchor returns False instead of raising.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).parents[1]
sys.path.insert(0, str(SCRIPTS))
import review_gate_publish as publish  # noqa: E402
from review_gate_github import GithubUnavailable  # noqa: E402

REPO, PR = "example-org/example-repo", 12
HEAD, OLD_HEAD = "b" * 40, "a" * 40
VERDICT_CONTEXT, PRESENCE_CONTEXT = "llm-review", "llm-review-present"
CONTEXTS = (VERDICT_CONTEXT, PRESENCE_CONTEXT)
PULL = f"repos/{REPO}/pulls/{PR}"
ISSUES = f"repos/{REPO}/issues/{PR}/comments"
STATUSES = f"repos/{REPO}/statuses/{HEAD}"
CHECK_RUNS = f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100&filter=latest"
FOUND_KEYS = ("severity", "category", "text", "path", "line", "fingerprint")


class FakeGh:
    """A fake ``corsia github``: records (argv, body) per call and answers from a router or a list.

    A list answer is consumed in call order and an Exception in it is raised, which is how a refused
    write is staged without ever reaching GitHub."""

    def __init__(self, answer):
        self.answer, self.calls, self.timeouts = answer, [], []

    def __call__(self, args, payload=None, timeout=None):
        self.calls.append((list(args), payload))
        self.timeouts.append(timeout)
        if callable(self.answer):
            item = self.answer(list(args), payload, timeout)
        elif isinstance(self.answer, list):
            item = self.answer.pop(0)
        else:
            item = self.answer
        if isinstance(item, BaseException):
            raise item
        return item

    @property
    def argv(self):
        return [args for args, _ in self.calls]

    def bodies(self):
        return [json.loads(payload) for _, payload in self.calls if payload]

    def body_for(self, *fragments):
        return [json.loads(payload) for args, payload in self.calls
                if payload and all(fragment in " ".join(args) for fragment in fragments)]

    def paths(self):
        return [args[1] for args, _ in self.calls]


def router(**by_fragment):
    """Answer a call by the first key that appears in its argv; a callable value builds the answer."""
    def answer(args, payload=None, timeout=None):
        joined = " ".join(args)
        for fragment, value in by_fragment.items():
            if fragment in joined:
                return value(args, payload) if callable(value) else value
        raise AssertionError(f"unexpected call: {joined}")
    return answer


def failed(message, status=None):
    return publish.GithubWriteFailed(message, status)


def threads_page(nodes, *, has_next=False, cursor=None, head=HEAD):
    """One page of GitHub's GraphQL review-thread answer."""
    return json.dumps({"data": {"repository": {"pullRequest": {
        "headRefOid": head,
        "reviewThreads": {"nodes": nodes, "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}}}}}})


def thread_node(thread_id, root_id, *, resolved=False, outdated=False, replies=()):
    return {"id": thread_id, "isResolved": resolved, "isOutdated": outdated,
            "comments": {"nodes": [{"databaseId": root_id}, *[{"databaseId": i} for i in replies]]}}


def comment(comment_id, body, *, login="review-bot", in_reply_to=None, path="a.py", line=3):
    """One REST review comment, in the shape ``repos/<repo>/pulls/<pr>/comments`` returns."""
    return {"id": comment_id, "body": body, "user": {"login": login}, "in_reply_to_id": in_reply_to,
            "path": path, "line": line, "commit_id": HEAD}


SETTINGS = publish.Settings("example-model", show_model_in_output=True, sme="maintainer")
FINDING = {"severity": "must_fix", "category": "correctness", "text": "Off by one.", "fingerprint": "f0" + "0" * 14,
           "path": "a.py", "line": 3, "repo": REPO, "head_sha": HEAD, "base_sha": OLD_HEAD}


def finding_body(overrides=None, settings=SETTINGS):
    return publish.render_finding({**FINDING, **(overrides or {})}, settings)


def raised(overrides=None):
    return {**{key: FINDING[key] for key in FOUND_KEYS}, **(overrides or {})}


def envelope(payload):
    return {"source": "outside", "kind": "qa.review", "valid": True, "repo": REPO, "pr": PR, "head_sha": HEAD,
            "run_id": "run-77", "workflow": "reviewer", "workflow_version": "v9", "model": "example-model", "payload": payload}


def client(answer, *, contexts=CONTEXTS):
    gh = FakeGh(answer)
    return publish.LiveClient(gh, login="review-bot", contexts=contexts), gh


def client(answer, *, contexts=CONTEXTS, run_seconds=None, clock=None, sleep=None):
    gh = FakeGh(answer)
    return publish.LiveClient(gh, login="review-bot", contexts=contexts, run_seconds=run_seconds,
                              clock=clock or __import__("time").monotonic, sleep=sleep or __import__("time").sleep), gh


def publishing(prior_comments=(), thread_nodes=(), issue_comments=(), refuses="", refuse_status=500, refuse_resolve=False, run_seconds=None, clock=None, sleep=None, drain=False):
    """A LiveClient answering one whole publish. ``refuses`` is a path fragment whose write is refused
    with ``refuse_status``; a reply's node lookup is answered from the first prior comment's id; a
    thread-state mutation is confirmed as asked unless ``refuse_resolve``; ``drain`` charges the
    fake clock for each call's timeout, which is how a run's deadline is exercised."""
    def answer(args, payload, timeout=None):
        if drain and clock is not None:
            clock.advance(float(timeout or 0))
        joined = " ".join(args)
        writing = args[-1] == "-"
        if args[1:2] == [PULL]:
            return json.dumps({"head": {"sha": HEAD}, "base": {"sha": OLD_HEAD, "ref": "dev"}})
        if args[1:2] == ["graphql"] and "PullRequestReviewThread" in joined:
            return json.dumps({"data": {"node": {"comments": {"nodes": [{"databaseId": prior_comments[0]["id"]}]},
                                              "pullRequest": {"number": PR}}}})
        if args[1:2] == ["graphql"] and "ReviewThread(input:" in joined:
            if refuse_resolve:
                return failed("gh: Could not resolve to a NODE with the id of PRRT_1 (HTTP 404)", 404)
            unresolve = "unresolveReviewThread(input:" in joined
            thread_id = next((arg.split("=", 1)[1] for arg in args if arg.startswith("threadId=")), "")
            return json.dumps({"data": {"unresolveReviewThread" if unresolve else "resolveReviewThread":
                                        {"thread": {"id": thread_id, "isResolved": not unresolve}}}})
        if args[1:2] == ["graphql"]:
            return threads_page(list(thread_nodes))
        if f"{PULL}/comments" in joined and not writing:
            return json.dumps(list(prior_comments))
        if ISSUES in joined and not writing:
            return json.dumps(list(issue_comments))
        if writing and refuses and refuses in joined:
            return failed(f"gh: refused (HTTP {refuse_status})", refuse_status)
        if "/statuses/" in joined:
            return json.dumps({"id": 5})
        if writing:
            return json.dumps({"id": 900})
        raise AssertionError(f"unexpected call: {joined}")
    return client(answer, run_seconds=run_seconds, clock=clock, sleep=sleep)


# --- reads -------------------------------------------------------------------------

def test_current_head_reads_the_pull_and_returns_its_head():
    live, gh = client(json.dumps({"head": {"sha": HEAD}, "base": {"ref": "dev"}}))
    assert live.current_head(REPO, PR) == HEAD
    assert gh.argv == [["api", PULL]]


def test_current_head_is_none_when_the_pull_is_unreadable():
    live, _ = client(failed("gh: Not Found (HTTP 404)", 404))
    assert live.current_head(REPO, PR) is None


def test_thread_states_argv_is_in_sources_query_with_is_outdated_added():
    live, gh = client(router(graphql=threads_page([])))
    assert live._thread_states(REPO, PR) == {}
    assert gh.argv == [["api", "graphql", "-F", "owner=example-org", "-F", "name=example-repo", "-F", "number=12",
                        "-f", f"query={publish.THREAD_STATES_QUERY}"]]
    assert "isResolved" in publish.THREAD_STATES_QUERY and "isOutdated" in publish.THREAD_STATES_QUERY


def test_list_reviewer_findings_returns_thread_state_per_thread_oldest_first():
    """Bodies and authorship come from the REST list, resolved/outdated from the thread snapshot.
    Only the agents' own marker threads count, a reply is its own row on the same thread id, and a
    thread GitHub did not name is skipped rather than guessed at."""
    ours, replied, theirs = FINDING["fingerprint"], "f1" + "0" * 14, "f2" + "0" * 14
    nodes = [thread_node("PRRT_ours", 401, resolved=True, outdated=True), thread_node("PRRT_reply", 402, replies=[412]),
             thread_node("PRRT_theirs", 403)]
    answers = router(graphql=threads_page(nodes), **{f"{PULL}/comments": json.dumps([
        comment(401, finding_body()),
        comment(402, finding_body({"fingerprint": replied})),
        comment(412, finding_body({"fingerprint": replied, "text": "Reworded."}), in_reply_to=402),
        comment(403, finding_body({"fingerprint": theirs}), login="maintainer"),
        comment(404, finding_body()),
        comment(405, "human prose, no marker", login="maintainer")])})
    live, gh = client(answers)
    assert live.list_reviewer_findings(REPO, PR) == [
        {"thread_id": "PRRT_ours", "body": finding_body(), "resolved": True, "outdated": True,
         "path": "a.py", "start_line": 3, "end_line": 3, "source_sha": HEAD, "refs": ["401"]},
        {"thread_id": "PRRT_reply", "body": finding_body({"fingerprint": replied}), "resolved": False, "outdated": False,
         "path": "a.py", "start_line": 3, "end_line": 3, "source_sha": HEAD, "refs": ["402", "412"]},
        {"thread_id": "PRRT_reply", "body": finding_body({"fingerprint": replied, "text": "Reworded."}),
         "resolved": False, "outdated": False,
         "path": "a.py", "start_line": 3, "end_line": 3, "source_sha": HEAD, "refs": ["402", "412"]}]
    assert gh.argv == [["api", "graphql", "-F", "owner=example-org", "-F", "name=example-repo", "-F", "number=12",
                        "-f", f"query={publish.THREAD_STATES_QUERY}"],
                       ["api", f"{PULL}/comments?per_page=100&page=1"]]


def test_list_reviewer_findings_reads_every_page_of_both_channels():
    first_nodes = [thread_node(f"PRRT_{i}", 1000 + i) for i in range(100)]
    rest_pages = {1: [comment(1000 + i, finding_body()) for i in range(100)], 2: [comment(2000, finding_body())]}

    def answer(args, payload, timeout=None):
        joined = " ".join(args)
        if args[1:2] == ["graphql"]:
            return threads_page([thread_node("PRRT_last", 2000)] if joined.endswith("cursor-1") else first_nodes,
                                has_next=not joined.endswith("cursor-1"),
                                cursor=None if joined.endswith("cursor-1") else "cursor-1")
        if joined.endswith("page=1"):
            return json.dumps(rest_pages[1])
        if joined.endswith("page=2"):
            return json.dumps(rest_pages[2])
        raise AssertionError(f"unexpected call: {joined[:90]}")

    live, gh = client(answer)
    rows = live.list_reviewer_findings(REPO, PR)
    assert len(rows) == 101 and rows[-1]["thread_id"] == "PRRT_last"
    assert gh.paths() == ["graphql", "graphql", f"{PULL}/comments?per_page=100&page=1",
                          f"{PULL}/comments?per_page=100&page=2"]
    assert gh.argv[1][-2:] == ["-F", "after=cursor-1"]


def test_thread_states_end_at_in_sources_guards():
    """In-source ends this read on a repeated root id, a head that moves mid-pagination, a stuck
    cursor or a malformed thread; here the failure surfaces rather than guessing thread state."""
    moving = [threads_page([thread_node("PRRT_1", 1)], has_next=True, cursor="c-1"),
              threads_page([thread_node("PRRT_2", 2)], head="c" * 40)]
    for answer in ([threads_page([thread_node("PRRT_1", 1)], has_next=True, cursor=None)], moving,
                   threads_page([thread_node("PRRT_1", 1), thread_node("PRRT_2", 1)]),
                   threads_page([{"id": "PRRT_1", "isResolved": None, "isOutdated": False,
                                  "comments": {"nodes": [{"databaseId": 1}]}}]),
                   threads_page([{"id": "PRRT_1", "isResolved": False,
                                  "comments": {"nodes": [{"databaseId": 1}]}}])):
        live, _ = client(answer if isinstance(answer, list) else [answer])
        with pytest.raises(GithubUnavailable):
            live._thread_states(REPO, PR)


def test_a_blinked_history_read_is_reread_within_its_budget(monkeypatch):
    """An unreadable comment history is re-read up to 8 times, 4s apart, because one brief GitHub error must not discard a finished review."""
    slept = []
    monkeypatch.setattr(publish.time, "sleep", slept.append)
    live, gh = client([failed("gh: blocked (HTTP 403)", 403), json.dumps([comment(1, finding_body())])])
    assert live._comments(f"{PULL}/comments") == [comment(1, finding_body())]
    assert len(gh.calls) == 2 and slept == [publish.LiveClient.READ_INTERVAL_S]


def test_the_read_budget_is_bounded_then_the_failure_surfaces(monkeypatch):
    monkeypatch.setattr(publish.time, "sleep", lambda seconds: None)
    live, gh = client([failed("gh: blocked (HTTP 403)", 403)] * publish.LiveClient.READ_ATTEMPTS)
    with pytest.raises(GithubUnavailable):
        live._comments(f"{PULL}/comments")
    assert len(gh.calls) == publish.LiveClient.READ_ATTEMPTS


# --- writes ------------------------------------------------------------------------

def test_post_finding_anchors_on_the_head_path_and_line():
    live, gh = client(json.dumps({"id": 900}))
    assert live.post_finding(REPO, PR, HEAD, FINDING, "body text") is True
    assert gh.argv == [["api", f"{PULL}/comments", "-X", "POST", "--input", "-"]]
    assert gh.bodies() == [{"body": "body text", "commit_id": HEAD, "path": "a.py", "line": 3, "side": "RIGHT"}]


def test_post_finding_without_a_line_posts_on_the_file():
    live, gh = client(json.dumps({"id": 900}))
    live.post_finding(REPO, PR, HEAD, {**FINDING, "line": 0}, "body text")
    assert gh.bodies() == [{"body": "body text", "commit_id": HEAD, "path": "a.py", "subject_type": "file"}]


def test_post_finding_422_is_a_fallback_and_500_raises():
    rejected, _ = client(failed("gh: Invalid comment field. line 3 of a.py isn't part of the diff (HTTP 422)", 422))
    assert rejected.post_finding(REPO, PR, HEAD, FINDING, "body text") is False
    broken, _ = client(failed("gh: Server Error (HTTP 500)", 500))
    with pytest.raises(publish.GithubWriteFailed):
        broken.post_finding(REPO, PR, HEAD, FINDING, "body text")


def test_post_finding_with_no_path_never_calls_gh():
    live, gh = client(json.dumps("{}"))
    assert live.post_finding(REPO, PR, HEAD, {**FINDING, "path": ""}, "body text") is False
    assert gh.calls == []


def test_reply_maps_the_thread_to_its_root_comment_then_replies():
    target = json.dumps({"data": {"node": {"comments": {"nodes": [{"databaseId": 401}]},
                                             "pullRequest": {"number": PR}}}})
    live, gh = client([target, json.dumps({"id": 901})])
    live.reply(REPO, "PRRT_example", "Still open: evidence")
    assert gh.argv[0] == ["api", "graphql", "-f", f"query={publish.REPLY_TARGET_QUERY}",
                          "-f", "id=PRRT_example"]
    assert gh.argv[1] == ["api", f"{PULL}/comments", "-X", "POST", "--input", "-"]
    assert gh.bodies()[0] == {"body": "Still open: evidence", "in_reply_to": 401}


def test_reply_refuses_a_thread_that_is_not_addressable():
    for answer in (json.dumps({"data": {"node": None}}),
                   json.dumps({"data": {"node": {"comments": {"nodes": []}, "pullRequest": {"number": PR}}}})):
        live, gh = client([answer])
        with pytest.raises(GithubUnavailable):
            live.reply(REPO, "PRRT_missing", "text")
        assert len(gh.calls) == 1


def test_upsert_summary_edits_the_comment_that_carries_todays_marker():
    existing = json.dumps([{"id": 501, "body": f"an older summary\n\n{publish.REVIEW_SUMMARY_MARKER}"}])
    live, gh = client(router(**{f"{ISSUES}?": existing, "issues/comments/501": json.dumps({"id": 501})}))
    live.upsert_summary(REPO, PR, "### Review: no issues found")
    assert gh.argv == [["api", f"{ISSUES}?per_page=100&page=1"],
                       ["api", f"repos/{REPO}/issues/comments/501", "-X", "PATCH", "--input", "-"]]
    assert gh.bodies() == [{"body": "### Review: no issues found\n\n" + publish.REVIEW_SUMMARY_MARKER}]


def test_upsert_summary_appends_todays_marker_once_and_posts_when_there_is_none():
    live, gh = client(router(**{f"{ISSUES}?": json.dumps([]), f"{ISSUES} -X POST": json.dumps({"id": 1})}))
    live.upsert_summary(REPO, PR, f"summary\n\n{publish.REVIEW_SUMMARY_MARKER}")
    assert gh.argv[-1] == ["api", ISSUES, "-X", "POST", "--input", "-"]
    assert gh.bodies() == [{"body": f"summary\n\n{publish.REVIEW_SUMMARY_MARKER}"}]


def test_set_status_posts_the_context_it_configures():
    live, gh = client(router(statuses=json.dumps({"id": 5})))
    live.set_status(REPO, HEAD, VERDICT_CONTEXT, "failure", "1 must fix")
    assert gh.argv == [["api", STATUSES, "--method", "POST", "--input", "-"]]
    assert gh.bodies() == [{"state": "failure", "description": "1 must fix", "context": VERDICT_CONTEXT}]


def test_set_status_refuses_a_context_the_rule_set_does_not_configure():
    for context in ("llm-review-shadow", "ci/build", ""):
        live, gh = client(json.dumps("{}"))
        with pytest.raises(ValueError, match="live mode never writes status context"):
            live.set_status(REPO, HEAD, context, "success", "text")
        assert gh.calls == []


def test_set_status_keeps_githubs_140_character_budget():
    live, gh = client(router(statuses=json.dumps({"id": 5})))
    live.set_status(REPO, HEAD, VERDICT_CONTEXT, "pending", "n" * 200)
    assert gh.bodies()[0]["description"] == "n" * publish.STATUS_LIMIT


def test_standing_state_reads_the_combined_status_as_gh_helpers_does():
    combined = "\n".join([json.dumps({"context": "ci/build", "state": "success"}),
                          json.dumps({"context": VERDICT_CONTEXT, "state": "failure"})])
    live, gh = client(combined)
    assert live.standing_state(REPO, HEAD, VERDICT_CONTEXT) == "failure"
    assert gh.argv == [["api", f"repos/{REPO}/commits/{HEAD}/status", "--paginate", "--jq", ".statuses[]"]]
    absent, _ = client(json.dumps({"statuses": []}))
    assert absent.standing_state(REPO, HEAD, VERDICT_CONTEXT) is None
    unreadable, _ = client(failed("gh: Server Error (HTTP 500)", 500))
    assert unreadable.standing_state(REPO, HEAD, VERDICT_CONTEXT) is None


def test_set_thread_resolved_sends_in_sources_mutation_and_verifies_the_answer():
    live, gh = client(json.dumps({"data": {"resolveReviewThread": {"thread": {"id": "PRRT_1", "isResolved": True}}}}))
    assert live.set_thread_resolved(REPO, "PRRT_1", True) is True
    assert gh.argv == [["api", "graphql",
                        "-f", "query=mutation($threadId: ID!) { resolveReviewThread(input: {threadId: $threadId}) "
                                "{ thread { id isResolved } } }",
                        "-F", "threadId=PRRT_1"]]
    assert [payload for _, payload in gh.calls] == [None]  # the -F flags carry it; no request body
    closed, _ = client(json.dumps({"data": {"unresolveReviewThread": {"thread": {"id": "PRRT_1", "isResolved": False}}}}))
    assert closed.set_thread_resolved(REPO, "PRRT_1", False) is True


def test_set_thread_resolved_is_false_when_github_does_not_confirm_the_state():
    for answer in (json.dumps({"errors": [{"message": "Could not resolve to a Node"}]}),
                   json.dumps({"data": {"resolveReviewThread": {"thread": {"id": "PRRT_2", "isResolved": True}}}}),
                   json.dumps({"data": {"resolveReviewThread": {"thread": {"id": "PRRT_1", "isResolved": False}}}}),
                   json.dumps({"data": {"resolveReviewThread": None}}),
                   "not json",
                   failed("gh: Could not resolve to a NODE with the id of PRRT_1 (HTTP 404)", 404)):
        live, _ = client(answer)
        assert live.set_thread_resolved(REPO, "PRRT_1", True) is False, answer


def test_failed_checks_names_the_failed_checks_only():
    runs = json.dumps({"check_runs": [{"name": "ci/build", "conclusion": "failure"},
                                      {"name": "ci/lint", "conclusion": "success"},
                                      {"name": VERDICT_CONTEXT, "conclusion": "failure"},
                                      {"name": "ci/e2e", "conclusion": None, "status": "queued"}]})
    live, gh = client(runs)
    assert live.failed_checks(REPO, HEAD) == ["ci/build"]
    assert gh.argv == [["api", CHECK_RUNS]]
    unreadable, _ = client(failed("gh: Server Error (HTTP 500)", 500))
    assert unreadable.failed_checks(REPO, HEAD) == []


# --- the negative criteria ----------------------------------------------------------

def test_an_absent_finding_is_replied_to_and_then_its_thread_resolved():
    """publish_absence_reply's order on the wire: the reply POST first, then the resolve mutation."""
    live, gh = publishing(prior_comments=[comment(401, finding_body())],
                          thread_nodes=[thread_node("PRRT_1", 401)])
    payload = {"verdict": "pass", "findings": [],
               "replies": [{"thread_id": "PRRT_1", "fingerprint": FINDING["fingerprint"],
                            "outcome": "dropped", "evidence": "The line is checked now."}]}
    result = publish.publish(envelope(payload), SETTINGS, live)
    assert (result.published, result.reason) == (True, "published")
    assert [body for body in gh.bodies() if "in_reply_to" in body] == [{"body": f"Finding dropped: The line is checked now.\n\n{publish.RESOLVED_REPLY_MARKER}",
                                                                       "in_reply_to": 401}]
    mutations = [(index, " ".join(args)) for index, (args, _) in enumerate(gh.calls) if "ReviewThread(input:" in " ".join(args)]
    assert len(mutations) == 1 and "-f query=mutation($threadId: ID!) { resolveReviewThread(input: {threadId: $threadId})" in mutations[0][1]
    reply_at = next(index for index, (_, body) in enumerate(gh.calls) if body and "in_reply_to" in body)
    assert reply_at < mutations[0][0]


def test_a_still_raised_finding_on_a_closed_thread_reopens_it_and_replies_there():
    """reopen_and_republish, not a fresh comment: the unresolve mutation lands before the reply, and
    nothing is posted as a new anchored comment."""
    live, gh = publishing(prior_comments=[comment(401, finding_body({"text": "Old wording."}))],
                          thread_nodes=[thread_node("PRRT_1", 401, resolved=True)])
    result = publish.publish(envelope({"verdict": "findings", "findings": [raised()]}), SETTINGS, live)
    assert (result.published, result.reason) == (True, "published")
    assert [body for body in gh.bodies() if "in_reply_to" in body] == [{"body": finding_body(),
                                                                       "in_reply_to": 401}]
    assert not [body for body in gh.bodies() if "commit_id" in body]
    reopen_at = next(index for index, (args, _) in enumerate(gh.calls) if "unresolveReviewThread(input:" in " ".join(args))
    reply_at = next(index for index, (_, body) in enumerate(gh.calls) if body and "in_reply_to" in body)
    assert reopen_at < reply_at


def test_a_refused_reopen_falls_back_to_a_fresh_comment_on_the_head():
    body = finding_body()
    live, gh = publishing(prior_comments=[comment(401, finding_body({"text": "Old wording."}))],
                          thread_nodes=[thread_node("PRRT_1", 401, resolved=True)], refuse_resolve=True)
    result = publish.publish(envelope({"verdict": "findings", "findings": [raised()]}), SETTINGS, live)
    assert (result.published, result.reason) == (True, "published")
    assert not [body for body in gh.bodies() if "in_reply_to" in body]
    assert gh.body_for(f"{PULL}/comments") == [{"body": body, "commit_id": HEAD, "path": "a.py", "line": 3, "side": "RIGHT"}]


def test_an_outdated_still_raised_finding_is_reposted_at_the_current_head():
    """When GitHub says the prior thread is outdated and unresolved, the finding is
    posted fresh on the current head, not replied to in the stale thread."""
    body = finding_body()
    live, gh = publishing(prior_comments=[comment(401, body)],
                          thread_nodes=[thread_node("PRRT_example", 401, outdated=True)])
    result = publish.publish(envelope({"verdict": "findings", "findings": [raised()]}), SETTINGS, live)
    assert (result.published, result.reason) == (True, "published")
    assert gh.paths() == [PULL, "graphql", f"{PULL}/comments?per_page=100&page=1", f"{PULL}/comments",
                          f"{ISSUES}?per_page=100&page=1", ISSUES, STATUSES, STATUSES]
    summary = gh.body_for(ISSUES, "-X POST")[0]["body"]
    assert summary.startswith("### Review: 1 must-fix item") and summary.endswith(publish.REVIEW_SUMMARY_MARKER)
    assert gh.body_for(f"{PULL}/comments")[0] == {"body": body, "commit_id": HEAD, "path": "a.py",
                                                  "line": 3, "side": "RIGHT"}
    assert not [body for body in gh.bodies() if "in_reply_to" in body]
    assert [body["context"] for body in gh.bodies() if "context" in body] == [VERDICT_CONTEXT, PRESENCE_CONTEXT]


def test_a_changed_open_finding_is_replied_to_in_its_thread():
    live, gh = publishing(prior_comments=[comment(401, finding_body({"text": "Old wording."}))],
                          thread_nodes=[thread_node("PRRT_1", 401)])
    result = publish.publish(envelope({"verdict": "findings", "findings": [raised()]}), SETTINGS, live)
    assert (result.published, result.reason) == (True, "published")
    assert [body for body in gh.bodies() if "in_reply_to" in body] == [{"body": finding_body(),
                                                                        "in_reply_to": 401}]


def test_a_rejected_anchor_lands_in_the_summaries_fallback_section():
    body = finding_body()
    live, gh = publishing(prior_comments=[comment(401, body)],
                          thread_nodes=[thread_node("PRRT_1", 401, outdated=True)],
                          refuses=f"{PULL}/comments", refuse_status=422)
    result = publish.publish(envelope({"verdict": "findings", "findings": [raised()]}), SETTINGS, live)
    assert (result.published, result.reason) == (True, "published")
    summary = gh.body_for(ISSUES, "-X POST")[0]["body"]
    assert "Findings not attached to a changed line" in summary and body in summary
    assert gh.paths()[-3:] == [ISSUES, STATUSES, STATUSES]


def test_a_failed_post_leaves_the_verdict_readable_and_the_status_attempted():
    """publish_failure: the summary write refuses. What survives is the error status on the verdict
    context naming the reason, the qa.review.completed event carrying the verdict and its counts, and
    the finding that was already posted."""
    body = finding_body()
    live, gh = publishing(prior_comments=[comment(401, body)],
                          thread_nodes=[thread_node("PRRT_1", 401, outdated=True)],
                          refuses=ISSUES)
    payload = {"verdict": "findings", "findings": [raised()],
               "error": "worker detail: the model answer was unreadable"}
    result = publish.publish(envelope(payload), SETTINGS, live)
    assert result.reason == "publish_failure:GithubWriteFailed: gh: refused (HTTP 500)"
    [completed] = result.events
    assert completed["type"] == "qa.review.completed" and completed["verdict"] == "error"
    assert completed["blocks"] == 1 and completed["flags"] == 0
    statuses = [status for status in gh.bodies() if "context" in status]
    assert [status["state"] for status in statuses] == ["error"] and statuses[0]["context"] == VERDICT_CONTEXT
    assert statuses[0]["description"].startswith("Review couldn't finish · retrying · publish_failure:GithubWriteFailed")
    assert "gh: refused (HTTP 500)" in statuses[0]["description"]
    assert len(statuses[0]["description"]) <= publish.STATUS_LIMIT
    assert [post["path"] for post in gh.body_for(f"{PULL}/comments")] == ["a.py"]
    writes = [args[1] for args, _ in gh.calls if args[-1] == "-"]
    assert writes == [f"{PULL}/comments", ISSUES, STATUSES]  # the finding landed, the summary refused, the error status attempted


def test_live_mode_publishes_everything_through_the_client():
    live, gh = publishing()
    result = publish.publish(envelope({"verdict": "pass", "findings": []}), SETTINGS, live)
    assert (result.published, result.reason) == (True, "published")
    assert gh.paths() == [PULL, "graphql", f"{PULL}/comments?per_page=100&page=1", f"{ISSUES}?per_page=100&page=1",
                          ISSUES, STATUSES, STATUSES]


# --- the resolution evidence for a prior the worker said nothing about --------------------

CITED = publish.Prior("PRRT_1", "f0" + "0" * 14, "must_fix", None, False, False, "a.py", 2, 5, OLD_HEAD)
UNCHANGED = "Cited lines unchanged since finding commit (`" + "a" * 8 + "` → `" + "b" * 8 + "`); not verified fixed."
CHANGED = UNCHANGED.replace("unchanged", "changed")


def patch_of(patch=None, error=""):
    def cited_patch(source, target, path):
        return patch, error
    return cited_patch


def test_the_not_raised_reply_is_in_sources_body_with_its_marker():
    assert publish.build_resolve_reply_body(UNCHANGED) == f"Not raised this run. {UNCHANGED}\n\n{publish.RESOLVED_REPLY_MARKER}"


def test_the_cited_line_report_names_what_the_diff_shows():
    assert publish.cited_line_report(CITED, HEAD, patch_of("")) == UNCHANGED  # the file didn't change
    elsewhere = "@@ -20,3 +20,3 @@\n ctx\n-removed\n+added\n ctx"
    assert publish.cited_line_report(CITED, HEAD, patch_of(elsewhere)) == UNCHANGED
    cited = "@@ -2,3 +2,2 @@\n ctx\n-removed\n ctx"
    assert publish.cited_line_report(CITED, HEAD, patch_of(cited)) == CHANGED  # a deletion in the cited range
    added = "@@ -2,2 +2,3 @@\n ctx\n+added\n ctx"
    assert publish.cited_line_report(CITED, HEAD, patch_of(added)) == CHANGED  # an insertion inside it
    outside = "@@ -9,2 +9,3 @@\n ctx\n+added\n ctx"
    assert publish.cited_line_report(CITED, HEAD, patch_of(outside)) == UNCHANGED  # the same edit elsewhere
    failure = "Could not check cited lines: finding commit does not precede reviewed head; not verified fixed."
    assert publish.cited_line_report(CITED, HEAD, patch_of(None, "finding commit does not precede reviewed head")) == failure


def test_the_cited_patch_is_read_in_the_checkout_however_many_files_changed(tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "")
    git("config", "user.name", "t")
    (repo / "a.py").write_text("one\ntwo\nthree\nfour\nfive\nsix\n")
    git("add", "-A")
    git("commit", "-q", "-m", "first")
    first = git("rev-parse", "HEAD")
    for index in range(310):  # past GitHub's 300-file comparison limit
        (repo / f"f{index}.txt").write_text(str(index))
    git("add", "-A")
    git("commit", "-q", "-m", "many files")
    many = git("rev-parse", "HEAD")
    (repo / "a.py").write_text("one\nTWO\nthree\nfour\nfive\nsix\n")
    git("commit", "-q", "-am", "edit a")
    edited = git("rev-parse", "HEAD")
    prior = publish.Prior("PRRT_1", "f", "must_fix", None, False, False, "a.py", 2, 2, first)
    report = publish.cited_line_report

    def patch(source, head, path):
        return publish.checkout_patch(repo, source, head, path)

    assert report(prior, many, patch).startswith("Cited lines unchanged since finding commit")
    assert report(prior, edited, patch).startswith("Cited lines changed since finding commit")
    later = publish.Prior("PRRT_1", "f", "must_fix", None, False, False, "a.py", 2, 2, edited)
    assert "does not precede reviewed head" in report(later, first, patch)


def test_a_prior_with_nothing_to_cite_or_no_exact_sha_says_so():
    called = []

    def counting(source, target):
        called.append((source, target))
        return {"status": "ahead", "files": []}, ""

    no_lines = publish.Prior("t", "f", "must_fix", None, False, False, "", None, None, OLD_HEAD)
    assert publish.cited_line_report(no_lines, HEAD, counting) == "No cited lines; not verified fixed."
    no_sha = publish.Prior("t", "f", "must_fix", None, False, False, "a.py", 2, 5, "not-a-sha")
    assert "finding commit is unavailable" in publish.cited_line_report(no_sha, HEAD, counting)
    same = publish.Prior("t", "f", "must_fix", None, False, False, "a.py", 2, 5, HEAD)
    assert publish.cited_line_report(same, HEAD, counting) == UNCHANGED.replace("aaaaaaaa", "bbbbbbbb")
    assert called == []  # the same commit answers for itself: nothing is compared


def test_a_silent_prior_is_replied_to_with_its_evidence_then_resolved():
    """publish_absence_reply on the wire: the cited-line reply lands first, the resolve follows, and the
    evidence came from the cited file's diff between the two exact commits the finding and the review name."""
    prior_comment = {**comment(401, finding_body()), "original_commit_id": OLD_HEAD,
                     "original_start_line": 3, "original_line": 3}
    asked = []

    def cited_patch(repo):
        def read(source, head, path):
            asked.append((repo, source, head, path))
            return "", ""
        return read

    live, gh = publishing(prior_comments=[prior_comment], thread_nodes=[thread_node("PRRT_1", 401)])
    live.cited_patch = cited_patch
    result = publish.publish(envelope({"verdict": "pass", "findings": []}), SETTINGS, live)
    assert (result.published, result.reason) == (True, "published")
    replies = [body for body in gh.bodies() if "in_reply_to" in body]
    assert len(replies) == 1 and replies[0]["body"].startswith("Not raised this run. Cited lines unchanged since finding commit")
    assert replies[0]["body"].endswith(publish.RESOLVED_REPLY_MARKER)
    assert asked == [(REPO, OLD_HEAD, HEAD, "a.py")]
    mutations = [" ".join(args) for args, _ in gh.calls if "ReviewThread(input:" in " ".join(args)]
    assert len(mutations) == 1 and "resolveReviewThread(input:" in mutations[0]
    reply_at = next(index for index, (args, body) in enumerate(gh.calls) if body and "in_reply_to" in body)
    resolve_at = next(index for index, (args, _) in enumerate(gh.calls) if "ReviewThread(input:" in " ".join(args))
    assert reply_at < resolve_at


def test_a_worker_reply_on_the_thread_suppresses_the_scripted_one():
    payload = {"verdict": "pass", "findings": [],
               "replies": [{"thread_id": "PRRT_1", "fingerprint": "f0" + "0" * 14, "outcome": "still_open",
                            "evidence": "Still reachable at line 3."}]}
    live, gh = publishing(prior_comments=[{**comment(401, finding_body()), "original_commit_id": OLD_HEAD}],
                          thread_nodes=[thread_node("PRRT_1", 401)])
    asked = []
    live.cited_patch = lambda repo: lambda *args: asked.append(args) or ("", "")
    result = publish.publish(envelope(payload), SETTINGS, live)
    assert (result.published, result.reason) == (True, "published")
    assert [body["body"] for body in gh.bodies() if "in_reply_to" in body] == [f"Still open: Still reachable at line 3.\n\n{publish.RESOLVED_REPLY_MARKER}"]
    assert not [args for args, _ in gh.calls if "ReviewThread(input:" in " ".join(args)]
    assert asked == []  # the worker's own evidence stands; the cited lines are not re-read


def test_a_worker_reply_citing_a_comment_id_reaches_its_thread():
    """The staged conversation names comments, not threads. Any comment or node ID on the
    thread, including the author's reply, addresses that thread."""
    for cited in ("401", "PRRC_author", "402"):
        payload = {"verdict": "pass", "findings": [],
                   "replies": [{"thread_id": cited, "outcome": "still_open", "evidence": "Still reachable at line 3."}]}
        live, gh = publishing(prior_comments=[{**comment(401, finding_body()), "original_commit_id": OLD_HEAD},
                                              {**comment(402, "I fixed this.", login="dev", in_reply_to=401),
                                               "node_id": "PRRC_author"}],
                              thread_nodes=[thread_node("PRRT_1", 401, replies=[402])])
        result = publish.publish(envelope(payload), SETTINGS, live)
        assert (result.published, result.reason) == (True, "published"), cited
        lookups = [args for args, _ in gh.calls if "PullRequestReviewThread" in " ".join(args)]
        assert lookups and all("id=PRRT_1" in args for args in lookups), cited
        assert [body["body"] for body in gh.bodies() if "in_reply_to" in body] == [f"Still open: Still reachable at line 3.\n\n{publish.RESOLVED_REPLY_MARKER}"]


def test_a_worker_reply_naming_no_thread_is_left_out_and_the_verdict_still_publishes():
    payload = {"verdict": "pass", "findings": [],
               "replies": [{"thread_id": "999", "fingerprint": "unknown", "outcome": "dropped", "evidence": "Gone."}]}
    live, gh = publishing(prior_comments=[{**comment(401, finding_body()), "original_commit_id": OLD_HEAD}],
                          thread_nodes=[thread_node("PRRT_1", 401)])
    result = publish.publish(envelope(payload), SETTINGS, live)
    assert (result.published, result.reason) == (True, "published")
    replies = [body["body"] for body in gh.bodies() if "in_reply_to" in body]
    assert len(replies) == 1 and replies[0].startswith("Not raised this run.")
    assert not [args for args, _ in gh.calls if "id=999" in args]


# --- the run deadline ---------------------------------------------------------------------

class Clock:
    """A fake monotonic clock a test advances, so a run's deadline is observed without waiting."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_the_per_call_timeouts_are_in_sources_own_values():
    seen = []

    def runner(args, payload=None, timeout=None):
        joined = " ".join(args)
        seen.append((joined, timeout))
        if "ReviewThread(input:" in joined:
            return json.dumps({"data": {"resolveReviewThread": {"thread": {"id": "PRRT_1", "isResolved": True}}}})
        if "graphql" in joined:
            return threads_page([])
        if joined.endswith("--input -"):
            return json.dumps({"id": 1})
        if "?per_page=100&page=1" in joined:
            return json.dumps([])
        return json.dumps({"head": {"sha": HEAD}, "statuses": []})

    live = publish.LiveClient(runner, login="review-bot", contexts=CONTEXTS)
    live.current_head(REPO, PR)
    live._thread_states(REPO, PR)
    live._comments(f"{PULL}/comments")
    live.post_finding(REPO, PR, HEAD, {"path": "a.py", "line": 3}, "body")
    live.set_thread_resolved(REPO, "PRRT_1", True)
    live.set_status(REPO, HEAD, VERDICT_CONTEXT, "success", "done")

    def timeouts(fragment):
        return [timeout for joined, timeout in seen if fragment in joined]

    assert timeouts("reviewThreads") == [publish.READ_TIMEOUT_S]
    assert timeouts(f"{PULL}/comments?per_page") == [publish.READ_TIMEOUT_S]
    assert timeouts(f"{PULL}/comments -X POST") == [publish.WRITE_TIMEOUT_S]
    assert timeouts("ReviewThread(input:") == [publish.WRITE_TIMEOUT_S]
    assert timeouts("/statuses/") == [publish.WRITE_TIMEOUT_S]


def test_the_read_retry_budget_stops_at_the_run_deadline_instead_of_sleeping_past_it():
    clock, slept = Clock(), []

    def runner(args, payload=None, timeout=None):
        assert timeout is not None and timeout <= publish.READ_TIMEOUT_S
        clock.advance(float(timeout))  # every call uses its whole timeout
        raise publish.GithubWriteFailed("gh: Server Error (HTTP 500)", 500)

    live = publish.LiveClient(runner, login="review-bot", contexts=CONTEXTS, run_seconds=200.0,
                              clock=clock, sleep=slept.append)
    with pytest.raises(GithubUnavailable) as raised:
        live._comments(f"{PULL}/comments")
    assert "Server Error" in str(raised.value)  # the read's own failure surfaces, not a new error
    assert slept == [4.0, 4.0, 4.0]  # today's 8 x 4.0s budget, stopped at the deadline
    assert clock() <= 200.0 - publish.STATUS_RESERVE_S  # the reserve is what the deadline leaves behind


def test_the_reserve_is_kept_for_the_status_write_a_run_ends_on():
    clock, seen = Clock(), []

    def runner(args, payload=None, timeout=None):
        seen.append((" ".join(args), timeout))
        return json.dumps({"id": 1})

    live = publish.LiveClient(runner, login="review-bot", contexts=CONTEXTS, run_seconds=120.0,
                              clock=clock, sleep=lambda seconds: None)
    clock.now = 120.0 - publish.STATUS_RESERVE_S + 1.0  # 34s left: less than the reserve
    with pytest.raises(GithubUnavailable):
        live.post_finding(REPO, PR, HEAD, {"path": "a.py", "line": 3}, "body")
    live.set_status(REPO, HEAD, VERDICT_CONTEXT, "error", "Review couldn't finish")  # the reserved write runs
    assert seen == [(f"api repos/{REPO}/statuses/{HEAD} --method POST --input -", publish.WRITE_TIMEOUT_S)]
    clock.now = 120.0
    with pytest.raises(GithubUnavailable):
        live.set_status(REPO, HEAD, VERDICT_CONTEXT, "error", "Review couldn't finish")


def test_an_exhausted_run_still_posts_the_error_status_with_its_reason():
    clock = Clock()
    live, gh = publishing(run_seconds=100.0, clock=clock, sleep=lambda seconds: None, drain=True)
    result = publish.publish(envelope({"verdict": "findings", "findings": [raised()]}), SETTINGS, live)
    assert (result.published, result.reason) == (False, "publish_failure:GithubUnavailable: run time used up")
    assert result.events[0]["verdict"] == "error"
    assert [args[1] for args, _ in gh.calls if args[-1] == "-"] == [STATUSES]  # nothing else was attempted
    [status] = gh.bodies()
    assert status["state"] == "error" and status["context"] == VERDICT_CONTEXT
    assert "run time used up" in status["description"]
    assert clock() <= 100.0  # inside the timeout the run was registered with
