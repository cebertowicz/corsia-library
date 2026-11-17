"""stage_review (rule 3a) and publish's rule-run entry (rules 4 and 4b), against a fake read channel."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest
fixture = pytest.fixture
parametrize = pytest.mark.parametrize

SCRIPTS = Path(__file__).parents[1]
sys.path.insert(0, str(SCRIPTS))
import review_gate_config as rule_config  # noqa: E402
import review_gate_evaluate as evaluate  # noqa: E402
import review_gate_publish as publish  # noqa: E402
import review_gate_stage as stage  # noqa: E402
from review_gate_github import Github, GithubUnavailable  # noqa: E402

REPO = "example-org/example-repo"
HEAD = "b" * 40
BASE = "a" * 40
IDENTITY = rule_config.request_identity(REPO, 12, HEAD, 1)


class FakeGithub:
    """Answers the reads stage and publish make; records each request's arguments."""

    def __init__(self, head=HEAD, missing=(), comments=None, base_ref="dev", too_large=False):
        self.head, self.missing, self.calls, self.base_ref, self.too_large = head, set(missing), [], base_ref, too_large
        self.comments = comments if comments is not None else [{"id": 101}]

    def __call__(self, args):
        self.calls.append(args)
        path = args[0]
        if "/contents/" in path:
            name = path.split("/contents/")[1].split("?")[0]
            if name in self.missing:
                raise GithubUnavailable("HTTP 404")
            return f"standards {name}"
        if args[1:] == ["-H", "Accept: application/vnd.github.diff"]:
            if self.too_large:
                raise GithubUnavailable("GitHub read failed: gh: Sorry, the diff exceeded the maximum number of files (300). (HTTP 406)")
            return "diff --git a/x b/x\n"
        if path.startswith(f"repos/{REPO}/pulls/12?") or path == f"repos/{REPO}/pulls/12":
            return json.dumps({"head": {"sha": self.head}, "base": {"sha": BASE, "ref": self.base_ref}})
        if "/issues/12/comments" in path:
            return json.dumps(self.comments if "page=1" in path else [])
        if "/compare/" in path:
            return json.dumps({"status": "identical", "files": []})
        if "/pulls/12/comments" in path:
            return json.dumps([{"id": 202}] if "page=1" in path else [])
        if "/pulls/12/reviews" in path:
            return json.dumps([{"id": 303}] if "page=1" in path else [])
        raise AssertionError(f"unexpected read {args}")


@fixture
def config(tmp_path):
    loaded = rule_config.load()
    loaded["thread_root"] = str(tmp_path)
    loaded["repo_checkouts"] = {REPO: "source/example-repo"}
    loaded["agents_login"] = "review-bot"
    loaded["sme"] = "maintainer"
    loaded["mode"] = "shadow"  # independent of the installed mode; a test opts into live itself
    loaded.pop("standards_local_root", None)  # repository mode; the local mode has its own test
    return loaded


@fixture(autouse=True)
def heads(monkeypatch):
    """Each (checkout, sha, dest) stage asked for; the real git step has its own tests."""
    added = []

    def add(checkout, sha, dest):
        added.append((checkout, sha, dest))
        dest.mkdir(parents=True)

    monkeypatch.setattr(stage, "add_head_worktree", add)
    return added


@fixture(autouse=True)
def local_git(monkeypatch):
    """stage's local-diff and since-last-review steps; real git has its own tests."""
    asked = []

    def local(checkout, base_ref, sha):
        asked.append(("local", base_ref, sha))
        return "diff --git a/local b/local\n"

    def since(checkout, reviewed, sha):
        asked.append(("since", reviewed, sha))
        return "diff --git a/s b/s\n"

    monkeypatch.setattr(stage, "local_diff", local)
    monkeypatch.setattr(stage, "since_last_review", since)
    return asked


@fixture
def store(config):
    return evaluate.StateStore(rule_config.folder(config, "state"))


def claim(store, head=HEAD):
    with store.transaction() as state:
        evaluate.put(state, "in_flight", REPO, 12, head)


def events(lines):
    return [json.loads(line)["event"] for line in lines if '"event"' in line]


# --- stage_review -------------------------------------------------------------------

def test_stage_writes_inputs_records_ids_and_requests_the_worker(config, store, tmp_path, heads):
    claim(store)
    fake = FakeGithub()
    reason, lines = stage.stage({"request_identity": IDENTITY, "mode": "local"}, config, Github(fake), store)
    assert reason == "staged"
    [requested] = events(lines)
    assert requested["type"] == "qa.review_requested"
    facts = requested["facts"]
    assert "head_repo" not in facts and facts["mode"] == "local" and facts["attempt"] == 1
    folder = rule_config.folder(config, "staging") / facts["staging_path"]
    assert heads == [(rule_config.project_root(config) / "source/example-repo", HEAD, folder / "head")]
    assert folder.parent == tmp_path / "staging"
    assert (folder / "diff.patch").read_text() == "diff --git a/x b/x\n"
    assert sorted(p.name for p in (folder / "standards").iterdir()) == sorted(Path(p).name for p in config["standards"])
    assert store.snapshot()["staged"][IDENTITY] == ["101", "202", "303"]
    assert all(f"ref={BASE}" in call[0] for call in fake.calls if "/contents/" in call[0])


def test_stage_reads_standards_from_the_project_folder_when_configured(config, store, tmp_path):
    thread = tmp_path / "project" / ".threads" / "qg"
    config.update(thread_root=str(thread), standards_local_root="local-standards")
    local = tmp_path / "project" / "local-standards"
    local.mkdir(parents=True)
    for path in config["standards"]:
        (local / Path(path).name).write_text(f"local {Path(path).name}")
    store = evaluate.StateStore(rule_config.folder(config, "state"))
    claim(store)
    fake = FakeGithub()
    reason, lines = stage.stage({"request_identity": IDENTITY}, config, Github(fake), store)
    assert reason == "staged"
    folder = rule_config.folder(config, "staging") / events(lines)[0]["facts"]["staging_path"]
    assert (folder / "standards" / "review-standards.md").read_text() == "local review-standards.md"
    assert not any("/contents/" in call[0] for call in fake.calls)

    (local / "review-standards.md").unlink()
    claim(store)
    assert stage.stage({"request_identity": IDENTITY}, config, Github(fake), store)[0] == "stage_retry_bound"


@fixture(autouse=True)
def no_stage_wait(monkeypatch):
    monkeypatch.setattr(stage, "STAGE_RETRY_S", 0.0)


def test_a_failed_read_is_tried_again_in_the_run_then_raises_one_terminal(config, store):
    """Stage retries inside the run, then raises a terminal result."""
    claim(store)
    fake = FakeGithub(missing={config["standards"][0]})
    reason, lines = stage.stage({"request_identity": IDENTITY}, config, Github(fake), store)
    assert reason == "stage_retry_bound"
    assert not [e for e in events(lines) if e["type"] == "qa.review_stage_requested"]
    assert [e["facts"]["reason"] for e in events(lines) if e["type"] == "reviewer.terminal_error"] == \
        ["not_staged_after_retries"]
    pull_reads = [c for c in fake.calls if c == [f"repos/{REPO}/pulls/12"]]
    assert len(pull_reads) == config["no_result_retry_bound"]
    assert evaluate.get(store.snapshot(), "in_flight", REPO, 12) is None


def test_a_read_that_fails_once_stages_on_the_second_try(config, store):
    claim(store)
    fake = FakeGithub(missing={config["standards"][0]})
    original = fake.__call__
    failed = []

    def flaky(args):
        if "/contents/" in args[0] and not failed:
            failed.append(args)
            try:
                return original(args)  # still missing: this read fails once
            finally:
                fake.missing = set()
        return original(args)

    reason, lines = stage.stage({"request_identity": IDENTITY}, config, Github(flaky), store)
    assert reason == "staged"
    assert [e["type"] for e in events(lines)] == ["qa.review_requested"]


def test_a_failed_read_for_a_head_no_longer_in_flight_does_nothing(config, store):
    claim(store, head="c" * 40)
    fake = FakeGithub(missing={config["standards"][0]})
    assert stage.stage({"request_identity": IDENTITY}, config, Github(fake), store) == ("superseded", [])


def test_stage_reads_every_page(config, store):
    claim(store)
    fake = FakeGithub(comments=[{"id": n} for n in range(100)])
    original = fake.__call__

    def paged(args):
        if "/issues/12/comments" in args[0] and "page=2" in args[0]:
            fake.calls.append(args)
            return json.dumps([{"id": 999}])
        return original(args)

    assert stage.stage({"request_identity": IDENTITY}, config, Github(paged), store)[0] == "staged"
    assert "999" in store.snapshot()["staged"][IDENTITY]


def test_no_read_channel_counts_as_a_failed_attempt(config, store, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "no-client"))  # no corsia client on this run's PATH
    claim(store)
    reason, _ = stage.stage({"request_identity": IDENTITY}, config, Github(), store)
    assert reason == "stage_retry_bound"


def test_a_diff_github_refuses_as_too_large_is_written_from_the_checkout(config, store, local_git):
    claim(store)
    reason, lines = stage.stage({"request_identity": IDENTITY}, config, Github(FakeGithub(too_large=True)), store)
    assert reason == "staged"
    folder = rule_config.folder(config, "staging") / events(lines)[0]["facts"]["staging_path"]
    assert (folder / "diff.patch").read_text() == "diff --git a/local b/local\n"
    assert local_git == [("local", "dev", HEAD)]


def test_any_other_diff_failure_still_counts_as_a_failed_try(config, store, local_git):
    claim(store)
    fake = FakeGithub()
    original = fake.__call__

    def down(args):
        if args[1:] == ["-H", "Accept: application/vnd.github.diff"]:
            raise GithubUnavailable("GitHub read failed: HTTP 502")
        return original(args)

    assert stage.stage({"request_identity": IDENTITY}, config, Github(down), store)[0] == "stage_retry_bound"
    assert local_git == []


def test_a_re_review_gets_the_change_since_the_last_reviewed_head(config, store, local_git):
    claim(store)
    with store.transaction() as state:
        evaluate.put(state, "reviewed", REPO, 12, "e" * 40)
    _, lines = stage.stage({"request_identity": IDENTITY}, config, Github(FakeGithub()), store)
    folder = rule_config.folder(config, "staging") / events(lines)[0]["facts"]["staging_path"]
    assert (folder / "since-last-review.patch").read_text() == "diff --git a/s b/s\n"
    assert local_git == [("since", "e" * 40, HEAD)]


def test_a_first_review_has_no_since_last_review_patch(config, store, local_git):
    claim(store)
    _, lines = stage.stage({"request_identity": IDENTITY}, config, Github(FakeGithub()), store)
    folder = rule_config.folder(config, "staging") / events(lines)[0]["facts"]["staging_path"]
    assert not (folder / "since-last-review.patch").exists() and local_git == []


def test_superseded_head_stages_nothing(config, store):
    claim(store)
    reason, lines = stage.stage({"request_identity": IDENTITY}, config, Github(FakeGithub(head="c" * 40)), store)
    assert (reason, lines) == ("superseded", [])


# --- publish: rule 4 ---------------------------------------------------------------

def client(config, fake=None):
    return publish.ShadowClient(Github(fake or FakeGithub()), rule_config.folder(config, "shadow_record"),
                                "llm-review-shadow", rule_config.all_contexts(config))


def records(config):
    folder = rule_config.folder(config, "shadow_record")
    return [json.loads(line) for path in sorted(folder.glob("*.jsonl")) for line in path.read_text().splitlines()]


def result(payload, valid=True, event_type="worker.result"):
    return {"type": event_type, "facts": {
        "workflow": "pr-reviewer@3", "request_identity": IDENTITY, "run_id": "77", "valid": valid,
        "result": json.dumps(payload)}}


FINDING = {"severity": "must_fix", "category": "correctness", "text": "Off by one.", "path": "a.py", "line": 3,
           "rule_id": "E1"}


def test_result_publishes_to_the_shadow_record_only(config, store):
    reason, lines = publish.run_rule(result({"verdict": "findings", "findings": [FINDING]}), config, client(config), store)
    assert reason == "published"
    written = records(config)
    statuses = [r for r in written if r["write"] == "set_status"]
    assert {r["context"] for r in statuses} == {"llm-review-shadow"}
    assert any(r["write"] == "post_finding" for r in written)
    assert not any(e for e in events(lines) if e["type"] == "reviewer.verdict")  # shadow records it instead
    [verdict] = [r["facts"] for r in written if r["write"] == "reviewer.verdict"]
    assert verdict["verdict"] == "code_fail" and verdict["required_action"] == "none"
    assert verdict["block_count"] == 1 and verdict["finding_count"] == 1
    assert verdict["reviewer_standards"] == config["reviewer_standards"] and "summary" not in verdict


def test_live_mode_emits_reviewer_verdict(config, store):
    config["mode"] = "live"
    shadow = client(config)
    shadow.context = "llm-review"
    live_settings = {"verdict": "llm-review", "presence": None}
    config["status_contexts"]["live"] = live_settings
    _, lines = publish.run_rule(result({"verdict": "stop", "findings": [FINDING]}), config, shadow, store)
    [verdict] = [e["facts"] for e in events(lines) if e["type"] == "reviewer.verdict"]
    assert verdict["required_action"] == "approach_reset"


def test_fingerprint_ignores_text_so_a_reworded_finding_is_still_open():
    assert publish.fingerprint(FINDING) == publish.fingerprint({**FINDING, "text": "Off-by-one at the loop end."})
    assert publish.fingerprint(FINDING) != publish.fingerprint({**FINDING, "path": "b.py"})


@parametrize("event", [result({}, valid=False), result({}, event_type="worker.no_result")])
def test_no_or_invalid_result_writes_the_retrying_status(config, store, event):
    reason, _ = publish.run_rule(event, config, client(config), store)
    assert reason == "worker_error"
    [status] = [r for r in records(config) if r["write"] == "set_status"]
    assert status["state"] == "error" and "gh run rerun" not in status["description"]


def test_a_refused_start_writes_its_own_couldnt_finish_status(config, store):
    """A refused start's no_result names the bare workflow, with no version (rule 4-refused)."""
    event = result({}, event_type="worker.no_result")
    event["facts"].update(workflow="reviewer", reason="refused: example-model can't run now")
    reason, lines = publish.run_rule(event, config, client(config), store)
    assert reason == "worker_error"
    [status] = [r for r in records(config) if r["write"] == "set_status"]
    assert status["state"] == "error" and status["description"].startswith("Review couldn't finish")
    [completed] = [json.loads(line)["measure"]["attributes"] for line in lines]
    assert completed["verdict"] == "error" and "reviewer_version" not in completed
    # a settled result still needs its version
    finished = result({"verdict": "pass", "findings": []})
    finished["facts"]["workflow"] = "reviewer"
    assert not publish.validate_envelope(publish.envelope_from_worker(finished))[0]


def test_the_old_verdict_word_is_refused():
    assert not publish.validate_envelope(publish.envelope_from_worker(result({"verdict": "block", "findings": [FINDING]})))[0]


def test_no_result_error_status_names_the_settle_reason_and_detail(config, store):
    event = result({}, event_type="worker.no_result")
    event["facts"].update(reason="turn_failed", detail="Claude Code capability boundary violated")
    publish.run_rule(event, config, client(config), store)
    [status] = [r for r in records(config) if r["write"] == "set_status"]
    assert status["state"] == "error" and status["context"] == "llm-review-shadow"
    assert "worker_error: turn_failed: Claude Code capability boundary violated" in status["description"]


def test_an_invalid_result_fails_the_script_run(config, monkeypatch, capsys):
    """A worker result this script cannot read at all is a failed run, not a quiet success: the owner
    route is the only place its reason can reach."""
    event = result({"verdict": "pass", "findings": []})
    event["facts"]["workflow"] = ""  # no workflow version ever ran, and this is not an error verdict
    monkeypatch.setattr(rule_config, "load", lambda path=None: config)
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(event)))
    assert publish.rule_main() == 2
    assert "invalid_envelope" in capsys.readouterr().err


def test_result_for_a_superseded_head_is_not_published(config, store):
    claim(store, head="c" * 40)
    assert publish.run_rule(result({"verdict": "pass", "findings": []}), config, client(config), store)[0] == "superseded"
    assert records(config) == []


def test_the_worker_thread_reaches_the_completed_measure():
    run = result({"verdict": "pass", "findings": []})
    run["facts"]["worker_thread"] = "w1"
    line = json.loads(publish.measure_line(publish._completed(publish.envelope_from_worker(run))))
    assert line["measure"]["attributes"]["worker_thread"] == "w1"
    bare = publish._completed(publish.envelope_from_worker(result({"verdict": "pass", "findings": []})))
    assert "worker_thread" not in bare  # a start refused before spawning has none


class SummaryFails(publish.ShadowClient):
    """GitHub refuses the summary comment, so the verdict can't be posted."""

    def upsert_summary(self, *args, **kwargs):
        raise RuntimeError("summary")


def failing_client(config):
    return SummaryFails(Github(FakeGithub()), rule_config.folder(config, "shadow_record"), "llm-review-shadow")


def test_a_result_that_cant_be_posted_is_requested_again_then_ends_at_the_bound(config, store):
    """A settled result whose posting fails is requested again at the next attempt, and the status
    says retrying only while a retry will happen."""
    assert config["no_result_retry_bound"] == 2
    claim(store)
    reason, lines = publish.run_rule(result({"verdict": "pass", "findings": []}), config, failing_client(config), store)
    assert reason.startswith("publish_failure:")
    [stage_request] = [e["facts"] for e in events(lines) if e["type"] == "qa.review_stage_requested"]
    assert stage_request["attempt"] == 1 and stage_request["head_sha"] == HEAD  # no earlier attempts in this store
    first = [r for r in records(config) if r["write"] == "set_status"][-1]
    assert first["state"] == "error" and "retrying" in first["description"]
    reason, lines = publish.run_rule(result({"verdict": "pass", "findings": []}), config, failing_client(config), store)
    assert not [e for e in events(lines) if e["type"] == "qa.review_stage_requested"]
    assert [e["facts"]["reason"] for e in events(lines) if e["type"] == "qa.review_needs_turn"] == \
        ["terminal_not_published_after_retries"]  # Shadow brings the terminal to the owner.
    last = [r for r in records(config) if r["write"] == "set_status"][-1]
    assert last["state"] == "error" and "retrying" not in last["description"]


def test_a_posted_verdict_clears_the_publish_failure_count(config, store):
    publish.run_rule(result({"verdict": "pass", "findings": []}), config, failing_client(config), store)
    publish.run_rule(result({"verdict": "pass", "findings": []}), config, client(config), store)
    with store.transaction() as state:
        assert evaluate.get(state, "publish_failures", REPO, 12) is None


def test_a_no_result_is_left_to_evaluates_retry(config, store):
    _, lines = publish.run_rule(result({}, event_type="worker.no_result"), config, client(config), store)
    assert not [e for e in events(lines) if e["type"] == "qa.review_stage_requested"]


def test_a_superseded_result_is_not_requested_again(config, store):
    claim(store, head="c" * 40)
    _, lines = publish.run_rule(result({"verdict": "pass", "findings": []}), config, failing_client(config), store)
    assert lines == []


def test_a_status_queued_before_the_request_does_not_replace_it(config, store):
    """An older waiting status arrives after review was requested."""
    claim(store)
    shadow = client(config)
    assert publish.run_rule(status("waiting_checks"), config, shadow, store)[0] == "review_in_flight"
    assert publish.run_rule(status("requested"), config, shadow, store)[0] == "status_written"
    assert publish.run_rule(status("skipped"), config, shadow, store)[0] == "status_written"
    assert [r["description"] for r in records(config) if r["write"] == "set_status"] == \
        ["Review requested", "Review skipped by a maintainer"]


# --- publish: rule 4b --------------------------------------------------------------

def status(state="requested", head=HEAD):
    return {"type": "qa.review_status", "facts": {"repo": REPO, "pr": 12, "head_sha": head, "attempt": 1, "state": state}}


def test_status_is_written_once_and_never_over_a_verdict(config, store):
    shadow = client(config)
    assert publish.run_rule(status(), config, shadow, store)[0] == "status_written"
    assert publish.run_rule(status(), config, shadow, store)[0] == "unchanged"
    publish.run_rule(result({"verdict": "pass", "findings": []}), config, shadow, store)
    assert publish.run_rule(status("waiting_checks"), config, shadow, store)[0] == "verdict_already_posted"
    states = [r["state"] for r in records(config) if r["write"] == "set_status"]
    assert states == ["pending", "success"]


def test_conflicts_status_names_the_prs_base(config, store):
    event = status("conflicts")
    event["facts"]["base_ref"] = "dev"
    assert publish.run_rule(event, config, client(config), store)[0] == "status_written"
    written = [r for r in records(config) if r["write"] == "set_status"]
    assert [(r["state"], r["description"]) for r in written] == [("pending", "Waiting for conflicts with dev to be resolved")]


class FailedChecks(FakeGithub):
    def __init__(self, runs=None, **kwargs):
        super().__init__(**kwargs)
        self.runs = runs

    def __call__(self, args):
        if "/check-runs" in args[0]:
            self.calls.append(args)
            if self.runs is None:
                raise GithubUnavailable("HTTP 502")
            return json.dumps({"check_runs": self.runs})
        return super().__call__(args)


def test_a_failed_check_status_names_the_check(config, store):
    runs = [{"name": "UI-E2E (shard 2/3)", "conclusion": "failure"}, {"name": "Backend", "conclusion": "success"},
            {"name": "Lint", "conclusion": "cancelled"}, {"name": "llm-review", "conclusion": "failure"}]
    assert publish.run_rule(status("checks_failed"), config, client(config, FailedChecks(runs)), store)[0] == "status_written"
    [written] = [r for r in records(config) if r["write"] == "set_status"]
    assert (written["state"], written["description"]) == (
        "pending", "Failed: Lint, UI-E2E (shard 2/3) · review starts when checks pass")


def test_a_failed_check_status_still_writes_when_the_names_cant_be_read(config, store):
    assert publish.run_rule(status("checks_failed"), config, client(config, FailedChecks(None)), store)[0] == "status_written"
    [written] = [r for r in records(config) if r["write"] == "set_status"]
    assert written["description"] == "A check failed · review starts when checks pass"


def test_a_long_failed_check_list_fits_githubs_status_limit():
    names = [f"Very long check name number {n}" for n in range(10)]
    state, description = publish.status_for_state("checks_failed", publish.Settings(model=""), failed=names)
    assert len(description) <= publish.STATUS_LIMIT and description.endswith("review starts when checks pass")


def test_a_standing_success_is_never_replaced_by_a_status(config, store):
    shadow = client(config)
    shadow.set_status(REPO, HEAD, "llm-review-shadow", "success", "waived by a maintainer")
    assert publish.run_rule(status(), config, shadow, store)[0] == "success_stands"


def test_status_for_an_old_head_is_dropped(config, store):
    assert publish.run_rule(status(head="c" * 40), config, client(config), store)[0] == "not_current_head"


def test_shadow_client_refuses_any_other_context(config):
    with pytest.raises(ValueError):
        client(config).set_status(REPO, HEAD, "llm-review", "success", "x")


def test_findings_with_nothing_blocking_read_clean(config, store):
    """code_fail applies exactly when the posted status fails."""
    optional = {**FINDING, "severity": "optional"}
    publish.run_rule(result({"verdict": "findings", "findings": [optional]}), config, client(config), store)
    [verdict] = [r["facts"] for r in records(config) if r["write"] == "reviewer.verdict"]
    assert verdict["verdict"] == "clean" and verdict["nit_count"] == 1


def test_a_product_decision_reads_code_fail_with_no_must_fix(config, store):
    publish.run_rule(result({"verdict": "findings", "findings": [], "product_decision": "Changes who can merge."}),
                     config, client(config), store)
    [verdict] = [r["facts"] for r in records(config) if r["write"] == "reviewer.verdict"]
    assert verdict["verdict"] == "code_fail" and verdict["block_count"] == 0


class HeadUnreadable(FakeGithub):
    def __call__(self, args):
        if args[0] == f"repos/{REPO}/pulls/12":
            raise GithubUnavailable("GitHub read repos/example-org/example-repo/pulls/12 failed: TLS handshake timeout")
        return super().__call__(args)


def test_an_unreadable_head_still_publishes_the_verdict(config, store):
    """A TLS timeout on the head read must not drop a finished review (06:0xZ 11-06)."""
    reason, _ = publish.run_rule(result({"verdict": "pass", "findings": []}), config,
                                 client(config, HeadUnreadable()), store)
    assert reason == "published"
    written = records(config)
    assert [r["error"] for r in written if r["write"] == "head_unreadable"] == [
        "GitHub read repos/example-org/example-repo/pulls/12 failed: TLS handshake timeout"]
    assert [r["state"] for r in written if r["write"] == "set_status"] == ["success"]


def test_an_unreadable_head_still_writes_the_status(config, store):
    assert publish.run_rule(status(), config, client(config, HeadUnreadable()), store)[0] == "status_written"


def test_reader_runs_the_corsia_client_and_reports_its_error(monkeypatch, tmp_path):
    import review_gate_github as github_module
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return __import__("subprocess").CompletedProcess(argv, 1, "", "gh: Not Found (HTTP 404)\n")
    monkeypatch.setattr(github_module.subprocess, "run", run)
    with pytest.raises(GithubUnavailable, match="HTTP 404"):
        Github().text("repos/x/y/contents/a.md", accept="application/vnd.github.raw")
    assert seen == [["corsia", "github", "api", "repos/x/y/contents/a.md", "-H", "Accept: application/vnd.github.raw"]]


def test_reader_leaves_the_runs_environment_alone(monkeypatch):
    """The reader passes the run's own environment to the GitHub client."""
    import review_gate_github as github_module
    seen = []

    def run(argv, **kwargs):
        seen.append(kwargs)
        return __import__("subprocess").CompletedProcess(argv, 0, "{}", "")
    monkeypatch.setattr(github_module.subprocess, "run", run)
    Github().json("repos/x/y/pulls/1")
    assert "env" not in seen[0]


def test_live_mode_posts_everything_through_corsia_github(config, store, monkeypatch):
    """rule_main in live mode runs publish through LiveClient and every write is a ``corsia github
    api`` call. The fake sits at the subprocess boundary, so no test here can reach GitHub. Replaces
    the test that asserted the old 'scripts have no write channel' refusal, which the brief removes."""
    seen = []

    def answer(argv):
        if "graphql" in argv:
            return json.dumps({"data": {"repository": {"pullRequest": {
                "headRefOid": HEAD, "reviewThreads": {"nodes": [],
                                                      "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}})
        path = argv[3] if argv[:3] == ["corsia", "github", "api"] else ""
        if path == f"repos/{REPO}/pulls/12":
            return json.dumps({"head": {"sha": HEAD}, "base": {"sha": BASE, "ref": "dev"}})
        return "[]" if path.endswith("/comments?per_page=100&page=1") else "{}"

    def run(argv, **kwargs):
        seen.append((list(argv), kwargs.get("input")))
        return __import__("subprocess").CompletedProcess(argv, 0, answer(list(argv)), "")

    monkeypatch.setattr(publish.subprocess, "run", run)
    monkeypatch.setattr(rule_config, "load", lambda path=None: {**config, "mode": "live"})
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(result({"verdict": "findings",
                                                                                    "findings": [FINDING]}))))
    assert publish.rule_main() == 0
    writes = [(argv, body) for argv, body in seen if "--input" in argv]
    assert [argv[3] for argv, _ in writes] == [f"repos/{REPO}/pulls/12/comments", f"repos/{REPO}/issues/12/comments",
                                               f"repos/{REPO}/statuses/{HEAD}", f"repos/{REPO}/statuses/{HEAD}"]
    assert all(argv[:3] == ["corsia", "github", "api"] for argv, _ in seen)
    posted = [json.loads(body) for argv, body in writes if argv[3].endswith("pulls/12/comments")][0]
    assert posted["commit_id"] == HEAD and posted["path"] == "a.py" and posted["line"] == 3
    assert [json.loads(body)["context"] for argv, body in writes if "/statuses/" in argv[3]] == \
        ["llm-review", "llm-review-present"]
    assert not records(config), "live mode wrote to the shadow record"


def test_live_mode_emits_the_verdict_and_prints_no_notice(config, store, monkeypatch, capsys):
    """The lane-facing reviewer.verdict event is emitted live; owner notices are routed elsewhere, so
    publish prints nothing else to a lane."""
    def run(argv, **kwargs):
        if "graphql" in argv:
            body = json.dumps({"data": {"repository": {"pullRequest": {
                "headRefOid": HEAD, "reviewThreads": {"nodes": [],
                                                      "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}})
        else:
            path = argv[3] if argv[:3] == ["corsia", "github", "api"] else ""
            body = (json.dumps({"head": {"sha": HEAD}}) if path == f"repos/{REPO}/pulls/12"
                    else "[]" if path.endswith("page=1") else "{}")
        return __import__("subprocess").CompletedProcess(argv, 0, body, "")

    monkeypatch.setattr(publish.subprocess, "run", run)
    event = result({"verdict": "findings", "findings": [FINDING]})
    monkeypatch.setattr(rule_config, "load", lambda path=None: {**config, "mode": "live"})
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(event)))
    assert publish.rule_main() == 0
    printed = capsys.readouterr().out
    [verdict] = [json.loads(line)["event"] for line in printed.splitlines() if '"reviewer.verdict"' in line]
    assert verdict["facts"]["verdict"] == "code_fail" and verdict["facts"]["block_count"] == 1
    assert "notice" not in printed


def test_shadow_mode_still_writes_nothing_through_the_client(config, store, monkeypatch):
    """Shadow is unchanged: it still reads through the run's read channel and records every post
    locally, and it makes no write call at all (no JSON body is ever passed)."""
    seen = []

    def run(argv, **kwargs):
        seen.append((list(argv), kwargs.get("input")))
        path = argv[3] if argv[:3] == ["corsia", "github", "api"] else ""
        body = (json.dumps({"head": {"sha": HEAD}, "base": {"sha": BASE, "ref": "dev"}})
                if path == f"repos/{REPO}/pulls/12" else "[]")
        return __import__("subprocess").CompletedProcess(argv, 0, body, "")

    monkeypatch.setattr(publish.subprocess, "run", run)
    monkeypatch.setattr(rule_config, "load", lambda path=None: config)  # the fixture's thread_root, so records land in tmp
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(result({"verdict": "findings",
                                                                                    "findings": [FINDING]}))))
    assert publish.rule_main() == 0
    assert [input for _, input in seen if input is not None] == [], "shadow mode passed a write body"
    assert all(argv[3].startswith(f"repos/{REPO}/") for argv, _ in seen if argv[:3] == ["corsia", "github", "api"])
    written = records(config)
    assert {r["write"] for r in written} >= {"post_finding", "upsert_summary", "set_status", "reviewer.verdict"}
    assert {r["context"] for r in written if r["write"] == "set_status"} == {"llm-review-shadow"}


def test_publish_refuses_the_checks_the_install_schema_cannot_carry():
    """corsia.yaml's result schema has no minLength/minimum (install refuses them), so publish does."""
    def valid(finding):
        return publish.validate_envelope(publish.envelope_from_worker(result({"verdict": "findings", "findings": [finding]})))[0]
    assert valid(FINDING)
    assert not valid({**FINDING, "line": -1})
    assert not valid({**FINDING, "text": " "})


@parametrize("base_ref", ["dev", "main"])
def test_stage_names_the_base_so_exactly_one_start_rule_takes_the_head(config, store, base_ref):
    """G2: a PR into the extended-window base starts with profile main, any other with the base block."""
    claim(store)
    _, lines = stage.stage({"request_identity": IDENTITY}, config, Github(FakeGithub(base_ref=base_ref)), store)
    [requested] = events(lines)
    assert requested["facts"]["base_ref"] == base_ref
    starts = [r for r in json.loads(rule_config.CONFIG.read_text())["rules"] if r["action"] == "start_worker"]
    by_base = {r["id"]: [t for t in r["match"] if t["field"] == "base_ref"] for r in starts}
    assert by_base == {"3-start": [{"field": "base_ref", "op": "not_eq", "value": "$extended_window_base"}],
                       "3-start-main": [{"field": "base_ref", "op": "eq", "value": "$extended_window_base"}]}
    assert [r.get("worker_profile") for r in starts] == [None, "main"]


def test_a_head_that_cannot_be_checked_out_retries_and_requests_no_worker(config, store, monkeypatch):
    claim(store)
    tries = []

    def fail(checkout, sha, dest):
        tries.append(sha)
        raise stage.HeadUnavailable("git fetch origin bbbbbbbbbbbb: not our ref")

    monkeypatch.setattr(stage, "add_head_worktree", fail)
    reason, lines = stage.stage({"request_identity": IDENTITY}, config, Github(FakeGithub()), store)
    assert reason == "stage_retry_bound" and len(tries) == config["no_result_retry_bound"]
    assert not [e for e in events(lines) if e["type"] == "qa.review_requested"]
    assert IDENTITY not in store.snapshot()["staged"]


def test_no_rule_or_script_depends_on_the_removed_facts():
    """CORSIA no longer sets check_state, mergeable, current_head or head_repo for a rule; the scripts read GitHub themselves."""
    rules = json.loads(rule_config.CONFIG.read_text())["rules"]
    tested = {m["field"] for rule in rules for m in rule.get("match", [])}
    assert not tested & {"check_state", "mergeable", "head_repo", "current_head"}
    start = next(r for r in rules if r["id"] == "3-start")
    assert "head_repo" not in json.dumps(start)
