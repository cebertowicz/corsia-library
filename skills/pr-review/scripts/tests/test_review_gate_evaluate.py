from __future__ import annotations

import io
import json
from pathlib import Path
import re
import sys

import pytest
fixture = pytest.fixture
parametrize = pytest.mark.parametrize

SCRIPTS = Path(__file__).parents[1]
sys.path.insert(0, str(SCRIPTS))
import review_gate_config as rule_config  # noqa: E402
import review_gate_evaluate as gate  # noqa: E402
from review_gate_github import Github, GithubUnavailable  # noqa: E402

HEAD = "b" * 40
NEWER = "c" * 40
REPO = "example-org/example-repo"


@fixture
def config(tmp_path):
    loaded = rule_config.load()
    loaded["thread_root"] = str(tmp_path)
    loaded["agents_login"] = "review-bot"
    loaded["sme"] = "maintainer"
    loaded["mode"] = "shadow"  # independent of the installed mode; a test opts into live itself
    return loaded


@fixture
def store(config):
    return gate.StateStore(rule_config.folder(config, "state"))


def event(event_type="github.pr.synchronize", **facts):
    return {"type": event_type, "facts": {
        "repo": REPO, "pr": 12, "head_sha": HEAD,
        "head_repo": REPO, "base_repo": REPO, "draft": False,
        "labels": [],
        **facts,
    }}


class FakeGH:
    """The GitHub reads evaluate makes, answered from a table; every path read is recorded."""

    def __init__(self, conclusion="success", suites=("success",), mergeable=True, pulls=(), pull_head=HEAD,
                 fail=None, sides=None):
        self.runs = [{"name": "ci", "conclusion": conclusion, "completed_at": "2026-01-01T00:00:00Z"}]
        self.suites, self.mergeable, self.pulls, self.pull_head, self.fail = suites, mergeable, list(pulls), pull_head, fail
        self.paths: list[str] = []
        self.sides = sides  # (head repo full_name or None, base repo full_name) on the PR read

    def __call__(self, args):
        path = args[0]
        self.paths.append(path)
        if self.fail and self.fail in path:
            raise GithubUnavailable(f"GitHub read {path} failed: HTTP 502")
        match = re.search(r"[?&]page=(\d+)", path)
        page = int(match.group(1)) if match else 1
        if "/check-suites" in path:
            return json.dumps({"check_suites": [{"conclusion": c} for c in self.suites]})
        if "/check-runs" in path:
            return json.dumps({"total_count": len(self.runs), "check_runs": self.runs if page == 1 else []})
        if "/pulls?state=open" in path:
            return json.dumps(self.pulls[(page - 1) * 100:page * 100])
        if "/pulls/" in path:
            body = {"head": {"sha": self.pull_head}, "mergeable": self.mergeable}
            if self.sides:
                head_repo, base_repo = self.sides
                body = {"head": {"sha": self.pull_head, "repo": head_repo and {"full_name": head_repo}},
                        "base": {"repo": {"full_name": base_repo}}, "mergeable": self.mergeable}
            return json.dumps(body)
        raise AssertionError(f"unexpected read {path}")


def types(lines):
    return [json.loads(line)["event"]["type"] for line in lines]


def facts_of(lines, event_type):
    return [json.loads(line)["event"]["facts"] for line in lines
            if json.loads(line)["event"]["type"] == event_type]


def run(store, config, evt, gh=None):
    return gate.handle(evt, store, config, Github(gh or FakeGH()))


def worker(event_type, attempt=1, head=HEAD, **facts):
    return {"type": event_type, "facts": {
        "workflow": "pr-reviewer@1", "request_identity": rule_config.request_identity(REPO, 12, head, attempt),
        **facts}}


@parametrize("value", ["success", "skipped", "neutral", "all_green"])
def test_green_head_requests_staging_and_reports_requested(store, config, value):
    reason, lines = run(store, config, event(), FakeGH(conclusion="success" if value == "all_green" else value))
    assert reason == "requested"
    assert types(lines) == ["qa.review_stage_requested", "qa.review_status"]
    staged = facts_of(lines, "qa.review_stage_requested")[0]
    assert staged["request_identity"] == f"{REPO}#12@{HEAD}:1"
    assert staged["mode"] == "default"
    assert facts_of(lines, "qa.review_status")[0]["state"] == "requested"


@parametrize("conclusion,value,state", [("queued", "pending", "waiting_checks"),
                                                    ("failure", "failing", "checks_failed")])
def test_pending_and_failing_checks_wait(store, config, conclusion, value, state):
    reason, lines = run(store, config, event(), FakeGH(conclusion=conclusion))
    assert reason == f"checks_{value}"
    assert facts_of(lines, "qa.review_status")[0]["state"] == state


def test_unreadable_checks_need_a_turn(store, config):
    reason, lines = run(store, config, event(), FakeGH(fail="/check-runs"))
    assert reason == "checks_unreadable"
    assert types(lines) == ["qa.review_needs_turn"]


def test_check_state_is_read_from_the_head_not_from_a_fact(store, config):
    """A stale check_state or mergeable fact on the event changes nothing: evaluate reads GitHub."""
    gh = FakeGH(conclusion="failure")
    reason, _ = run(store, config, event(check_state="green", combined_check_state="all_green",
                                         mergeable="MERGEABLE"), gh)
    assert reason == "checks_failing"
    assert any(f"/commits/{HEAD}/check-runs" in path for path in gh.paths)


def test_checks_are_read_page_by_page(store, config):
    gh = FakeGH()
    gh.runs = [{"name": f"job-{n}", "conclusion": "success", "completed_at": "2026-01-01T00:00:00Z"}
               for n in range(100)] + [{"name": "last", "conclusion": "failure", "completed_at": "2026-01-01T00:00:00Z"}]
    paged = gh.__call__

    def reader(args):
        body = json.loads(paged(args))
        if "/check-runs" in args[0]:
            page = int(re.search(r"[?&]page=(\d+)", args[0]).group(1))
            body = {"total_count": len(gh.runs), "check_runs": gh.runs[(page - 1) * 100:page * 100]}
        return json.dumps(body)

    assert gate.handle(event(), store, config, Github(reader))[0] == "checks_failing"


def test_unreadable_checks_on_an_in_flight_head_keep_the_reply_without_a_turn(store, config):
    """A reply arrived while its head was in review and the checks read failed."""
    assert run(store, config, event())[0] == "requested"
    reply = {"type": "github.pr_comment.created", "facts": {
        "repo": REPO, "pr_number": 12, "current_head": HEAD, "author": "dev", "pr_author_login": "dev",
        "pr_author_origin": "inside", "has_reviewer_marker": False, "comment_id": 9,
        "labels": []}}
    reason, lines = run(store, config, reply, FakeGH(fail="/check-runs"))
    assert reason == "already_in_flight"
    assert lines == []
    assert store.snapshot()["replies"][REPO]["12"]["ids"] == ["9"]


def test_a_push_waits_for_its_pending_checks_without_a_turn(store, config):
    evt = {"type": "github.pr.synchronize", "facts": {"repo": REPO, "pr_number": 12, "sha": HEAD,
                                                     "author_origin": "inside", "labels": []}}
    reason, lines = run(store, config, evt, FakeGH(conclusion="in_progress"))
    assert reason == "checks_pending"
    assert types(lines) == ["qa.review_status"]
    assert facts_of(lines, "qa.review_status")[0]["state"] == "waiting_checks"


def test_action_required_reports_maintainer_checks_without_a_turn(store, config):
    reason, lines = run(store, config, event(suite_conclusions=["action_required"]))
    assert reason == "checks_action_required"
    assert types(lines) == ["qa.review_status"]
    assert facts_of(lines, "qa.review_status")[0]["state"] == "maintainer_checks"


def test_a_held_check_suite_read_from_github_reports_maintainer_checks(store, config):
    """A fork PR's workflow awaiting approval has a suite and no runs."""
    reason, lines = run(store, config, event(), FakeGH(suites=("success", "action_required")))
    assert reason == "checks_action_required"
    assert facts_of(lines, "qa.review_status")[0]["state"] == "maintainer_checks"


@parametrize("event_type,head_fact", [("github.pr.ready_for_review", "sha"),
                                                  ("github.head.checks_completed", "head_sha")])
def test_integration_current_facts_shape_requests_review(store, config, event_type, head_fact):
    """The facts supplied on PR and head check events, by name and value."""
    evt = {"type": event_type, "facts": {
        "repo": REPO, "pr_number": 12, head_fact: HEAD, "current_head": HEAD,
        "author_origin": "inside", "branch": "dev", "base_sha": NEWER,
        "draft": False, "labels": []}}
    assert run(store, config, evt)[0] == "requested"


@parametrize("event_type,id_fact", [("github.pr_comment.created", "comment_id"),
                                                ("github.review.submitted", "review_id")])
def test_integration_reply_shape_reviews_the_live_head(store, config, event_type, id_fact):
    """A reply carries no sha or head_sha; its head is current_head from the PR read."""
    evt = {"type": event_type, "facts": {
        "repo": REPO, "pr_number": 12, "current_head": HEAD, "author": "maintainer",
        "author_association": "OWNER", "pr_author_login": "dev", "pr_author_origin": "inside",
        "has_reviewer_marker": False, id_fact: 9, "draft": False,
        "labels": []}}
    reason, lines = run(store, config, evt)
    assert reason == "requested"
    assert facts_of(lines, "qa.review_stage_requested")[0]["head_sha"] == HEAD


def test_reply_whose_pr_read_failed_names_the_read(store, config):
    """A reply carries no head: evaluate reads the PR's live head, and a failed read names itself."""
    evt = {"type": "github.pr_comment.created", "facts": {
        "repo": REPO, "pr_number": 12, "author": "dev", "pr_author_login": "dev",
        "pr_author_origin": "inside", "has_reviewer_marker": False, "comment_id": 9}}
    reason, lines = run(store, config, evt, FakeGH(fail="/pulls/12"))
    assert reason == "reply_pr_read_failed"
    assert facts_of(lines, "qa.review_needs_turn") == [
        {"reason": "reply_pr_read_failed", "repo": REPO, "pr": 12}]


def test_a_reply_without_current_head_is_bound_to_the_prs_live_head(store, config):
    evt = {"type": "github.pr_comment.created", "facts": {
        "repo": REPO, "pr_number": 12, "author": "maintainer", "pr_author_login": "dev",
        "pr_author_origin": "inside", "has_reviewer_marker": False, "comment_id": 9, "labels": []}}
    reason, lines = run(store, config, evt, FakeGH(pull_head=NEWER))
    assert reason == "requested"
    assert facts_of(lines, "qa.review_stage_requested")[0]["head_sha"] == NEWER


def test_integration_held_check_state_reports_maintainer_checks(store, config):
    evt = event("github.head.checks_completed", suite_conclusions=["action_required"])
    reason, lines = run(store, config, evt)
    assert reason == "checks_action_required"
    assert facts_of(lines, "qa.review_status")[0]["state"] == "maintainer_checks"


def test_raw_checks_choose_newest_per_name_and_exclude_reviewer_contexts():
    assert gate.combined_check_state([
        {"name": "ci", "conclusion": "failure", "completed_at": "2026-01-01T00:00:00Z"},
        {"name": "ci", "conclusion": "success", "completed_at": "2026-01-02T00:00:00Z"},
        {"name": "llm-review", "conclusion": "failure", "completed_at": "2026-01-03T00:00:00Z"},
    ], {"llm-review"}) == "all_green"


def test_pr_event_for_a_superseded_head_is_a_noop(store, config):
    """A PR event's own head is sha; current_head is the PR's live head."""
    facts = {"repo": REPO, "pr_number": 12, "sha": HEAD, "current_head": NEWER,
             "author_origin": "inside", "draft": False, "labels": []}
    assert run(store, config, {"type": "github.pr.opened", "facts": facts}) == ("stale_head", [])


def test_checks_event_for_a_superseded_head_is_a_noop(store, config):
    """head.checks_completed keeps head_sha = the checked commit; current_head is the PR's live head."""
    facts = {"repo": REPO, "pr_number": 12, "head_sha": HEAD, "current_head": NEWER,
             "author_origin": "inside", "draft": False, "labels": []}
    assert run(store, config, {"type": "github.head.checks_completed", "facts": facts}) == ("stale_head", [])
    facts["current_head"] = HEAD
    assert run(store, config, {"type": "github.head.checks_completed", "facts": facts})[0] == "requested"


def test_checks_event_with_no_pull_request_is_a_silent_noop(store, config):
    """A settled branch head with no PR carries no pr_number and no current_head."""
    facts = {"repo": REPO, "head_sha": HEAD, "conclusion": "success"}
    gh = FakeGH(pulls=[{"number": 3, "head": {"sha": NEWER}}])
    assert run(store, config, {"type": "github.head.checks_completed", "facts": facts}, gh) == ("no_pr", [])
    assert not any("/check-runs" in path for path in gh.paths)


def fork_pull(number, head):
    return {"number": number, "head": {"sha": head, "repo": {"full_name": "someone/example-repo"}}}


def test_a_checks_event_with_no_pr_number_finds_the_fork_pr_by_its_head(store, config):
    """check_suite.pull_requests is empty across forks, so the PR is matched by head.sha in the open list."""
    facts = {"repo": REPO, "head_sha": HEAD, "author_origin": "outside", "labels": []}
    gh = FakeGH(pulls=[fork_pull(4, NEWER), fork_pull(9, HEAD)])
    reason, lines = run(store, config, {"type": "github.head.checks_completed", "facts": facts}, gh)
    assert reason == "screen_pending"  # reached the outside screen: the PR was found
    assert facts_of(lines, "qa.review_status")[0]["pr"] == 9
    assert f"repos/{REPO}/pulls?state=open&per_page=100&page=1" in gh.paths


def test_the_open_pr_list_is_read_past_its_first_page(store, config):
    facts = {"repo": REPO, "head_sha": HEAD, "author_origin": "inside", "labels": []}
    gh = FakeGH(pulls=[fork_pull(n, NEWER) for n in range(1, 101)] + [fork_pull(150, HEAD)])
    reason, lines = run(store, config, {"type": "github.head.checks_completed", "facts": facts}, gh)
    assert reason == "requested"
    assert facts_of(lines, "qa.review_stage_requested")[0]["pr"] == 150


def test_a_failed_open_pr_read_names_itself_rather_than_dropping_the_event(store, config):
    facts = {"repo": REPO, "head_sha": HEAD}
    reason, lines = run(store, config, {"type": "github.head.checks_completed", "facts": facts},
                        FakeGH(fail="/pulls?state=open"))
    assert reason == "pr_lookup_failed"
    assert facts_of(lines, "qa.review_needs_turn") == [{"reason": "pr_lookup_failed", "repo": REPO, "head_sha": HEAD}]


def checks_event(**facts):
    """A settled-checks event as CORSIA sends it when no rule names author_origin: no origin, no sides."""
    return {"type": "github.head.checks_completed", "facts": {
        "repo": REPO, "head_sha": HEAD, "pr_number": 12, "conclusion": "success", **facts}}


def test_a_checks_event_with_no_origin_reads_the_prs_sides_and_reviews_a_same_repo_pr(store, config):
    gh = FakeGH(sides=(REPO, REPO))
    reason, lines = run(store, config, checks_event(), gh)
    assert reason == "requested"
    assert facts_of(lines, "qa.review_stage_requested")[0]["pr"] == 12


@parametrize("sides", [("someone/example-repo", REPO), (None, REPO), None])
def test_a_checks_event_with_no_origin_still_screens_a_fork_deleted_fork_or_unread_sides(store, config, sides):
    reason, lines = run(store, config, checks_event(), FakeGH(sides=sides))
    assert reason == "screen_pending"
    assert facts_of(lines, "qa.review_status")[0]["state"] == "safety_check"


def test_an_event_origin_wins_over_the_prs_sides(store, config):
    gh = FakeGH(sides=(REPO, REPO))
    assert run(store, config, checks_event(author_origin="outside"), gh)[0] == "screen_pending"


def test_reviewed_and_in_flight_heads_are_noops(store, config):
    assert run(store, config, event())[0] == "requested"
    assert run(store, config, event()) == ("already_in_flight", [])
    run(store, config, worker("worker.result", result=json.dumps({"findings": []})))
    assert run(store, config, event()) == ("already_reviewed", [])


def test_draft_waits_for_checks(store, config):
    got, lines = run(store, config, event(draft=True))
    assert got == "draft"
    assert facts_of(lines, "qa.review_status")[0]["state"] == "waiting_checks"


def test_conflicting_pr_says_it_waits_on_conflicts_with_its_base(store, config):
    got, lines = run(store, config, event(branch="dev"), FakeGH(mergeable=False))
    assert got == "not_mergeable"
    status = facts_of(lines, "qa.review_status")[0]
    assert (status["state"], status["base_ref"]) == ("conflicts", "dev")


@fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(gate, "MERGEABLE_REREAD_S", 0.0)


class ComputingGH(FakeGH):
    """GitHub still computing mergeable for the first ``nulls`` PR reads, then MERGEABLE."""

    def __init__(self, nulls, **kw):
        super().__init__(mergeable=None, **kw)
        self.nulls = nulls

    def __call__(self, args):
        if "/pulls/" in args[0] and "state=open" not in args[0]:
            self.mergeable = None if self.nulls > 0 else True
            self.nulls -= 1
        return super().__call__(args)


def test_mergeable_computed_on_a_re_read_within_the_run_requests_review(store, config, no_wait):
    gh = ComputingGH(nulls=2)
    reason, lines = run(store, config, event(), gh)
    assert reason == "requested"
    assert sum("/pulls/12" in p for p in gh.paths) == 3
    assert "reviewer.terminal_error" not in types(lines)


def test_green_head_with_mergeable_never_computed_raises_one_terminal_error(store, config, no_wait):
    config["mode"] = "live"
    reasons = [run(store, config, event(), FakeGH(mergeable=None)) for _ in range(3)]
    assert [r for r, _ in reasons] == ["mergeability_unresolved"] * 3
    terminal = [facts_of(lines, "reviewer.terminal_error") for _, lines in reasons]
    assert terminal[0] == [{"repo": REPO, "pr_number": 12, "head_sha": HEAD, "reason": "mergeability_unresolved"}]
    assert terminal[1] == terminal[2] == []
    assert "qa.review_status" not in types(reasons[0][1])


def test_unknown_mergeability_on_a_head_in_flight_keeps_its_status(store, config, no_wait):
    config["mode"] = "live"
    with store.transaction() as state:
        gate.put(state, "in_flight", REPO, 12, HEAD)
    reason, lines = run(store, config, event(), FakeGH(mergeable=None))
    assert reason == "already_in_flight"
    assert "qa.review_status" not in types(lines)
    assert "reviewer.terminal_error" not in types(lines)


def test_a_marker_left_by_the_old_counter_still_raises(store, config, no_wait):
    config["mode"] = "live"
    with store.transaction() as state:
        gate.put(state, "mergeability", REPO, 12, {"head": HEAD, "n": 1})
    _, lines = run(store, config, event(), FakeGH(mergeable=None))
    assert facts_of(lines, "reviewer.terminal_error")[0]["reason"] == "mergeability_unresolved"


@parametrize("conclusion,state", [("failure", "checks_failed"), (None, "waiting_checks")])
def test_unknown_mergeability_while_checks_are_not_green_reports_the_checks(store, config, no_wait, conclusion, state):
    gh = FakeGH(conclusion=conclusion, mergeable=None)
    if conclusion is None:
        gh.runs = [{"name": "ci", "status": "in_progress", "conclusion": None, "started_at": "2026-01-01T00:00:00Z"}]
    reason, lines = run(store, config, event(), gh)
    assert facts_of(lines, "qa.review_status")[0]["state"] == state
    assert "reviewer.terminal_error" not in types(lines)


def test_an_unreadable_pr_counts_as_unknown_mergeability(store, config, no_wait):
    assert run(store, config, event(), FakeGH(fail="/pulls/12"))[0] == "mergeability_unresolved"


def test_outside_commit_waits_for_screen_and_safe_screen_allows_review(store, config):
    outside = event(head_repo="someone/example-repo")
    reason, lines = run(store, config, outside)
    assert reason == "screen_pending"
    assert facts_of(lines, "qa.review_status")[0]["state"] == "safety_check"
    screen = {"type": "worker.result", "facts": {
        "workflow": "github-pr-screen@1", "repo": REPO, "pr_number": 12, "item_version": HEAD,
        "valid": True, "result": json.dumps({"payload": {"verdict": "safe"}})}}
    assert run(store, config, screen)[0] == "screen_safe"
    assert run(store, config, outside)[0] == "requested"


@parametrize("event_type,facts", [
    ("worker.result", {"valid": True, "result": json.dumps({"payload": {"verdict": "flagged"}})}),
    ("worker.result", {"valid": False, "result": "{}"}),
    ("worker.no_result", {}),
])
def test_unsafe_or_missing_screen_holds(store, config, event_type, facts):
    screen = {"type": event_type, "facts": {
        "workflow": "github-pr-screen@1", "repo": REPO, "pr_number": 12, "item_version": HEAD, **facts}}
    reason, lines = run(store, config, screen)
    assert reason == "screen_held"
    assert facts_of(lines, "qa.review_status")[0]["state"] == "held"
    assert run(store, config, event(head_repo="someone/example-repo"))[0] == "screen_held"


def test_a_refused_screen_start_holds_and_never_reads_as_a_review(store, config):
    """A refused start names the bare workflow, with no @version (rule 6a-screen-refused)."""
    screen = {"type": "worker.no_result", "facts": {
        "workflow": "github-pr-screen", "repo": REPO, "pr_number": 12, "item_version": HEAD}}
    reason, lines = run(store, config, screen)
    assert reason == "screen_held" and "qa.review_needs_turn" not in types(lines)


@parametrize("event_type", ["github.pr.closed", "github.pr.merged"])
def test_an_ended_pr_drops_its_claim_and_counters_but_keeps_reviewed(store, config, event_type):
    with store.transaction() as state:
        for marker in gate.END_CLEARS:
            gate.put(state, marker, REPO, 12, HEAD if marker == "in_flight" else {"head": HEAD, "n": 1})
        gate.put(state, "reviewed", REPO, 12, HEAD)
        gate.put(state, "in_flight", REPO, 13, HEAD)
    reason, lines = run(store, config, {"type": event_type, "facts": {"repo": REPO, "pr_number": 12, "action": "closed"}})
    assert (reason, lines) == ("pr_ended", [])
    snapshot = store.snapshot()
    assert all(gate.get(snapshot, marker, REPO, 12) is None for marker in gate.END_CLEARS)
    assert gate.get(snapshot, "reviewed", REPO, 12) == HEAD and gate.get(snapshot, "in_flight", REPO, 13) == HEAD
    assert run(store, config, {"type": event_type, "facts": {"repo": REPO}})[0] == "missing_or_invalid_identity"


def test_missing_fork_side_is_treated_as_outside(store, config):
    evt = event()
    del evt["facts"]["head_repo"]
    assert run(store, config, evt)[0] == "screen_pending"


@parametrize("pr_side", [{"pr_author_origin": "outside"}, {"head_repo": None}])
def test_an_inside_reply_on_an_outside_pr_never_skips_the_screen(store, config, pr_side):
    """A reply's author_origin is the commenter's; only the PR's origin decides step 4."""
    reply = event("github.pr_comment.created", author_origin="inside", comment_id=5, **pr_side)
    assert run(store, config, reply)[0] == "screen_pending"


def test_a_reply_on_an_inside_pr_reads_the_prs_origin(store, config):
    reply = event("github.pr_comment.created", author_origin="outside", pr_author_origin="inside",
                  head_repo="someone/example-repo", comment_id=5)
    assert run(store, config, reply)[0] == "reply_not_for_review"


@parametrize("event_type", ["github.pr.labeled", "github.pr.unlabeled"])
def test_a_label_never_starts_a_review(store, config, event_type):
    assert run(store, config, event(event_type, labels=["qa/review-local"])) == ("label_only", [])


def test_override_label_skips_before_anything_else(store, config):
    gh = FakeGH(fail="/commits/")
    reason, lines = run(store, config, event("github.pr.labeled", labels=["qa/override-reviewer"]), gh)
    assert gh.paths == []
    assert reason == "override_skip"
    assert facts_of(lines, "qa.review_status")[0]["state"] == "skipped"


def test_local_mode_label_sets_mode(store, config):
    _, lines = run(store, config, event(labels=["qa/review-local", "qa/review-parallel"]))
    assert facts_of(lines, "qa.review_stage_requested")[0]["mode"] == "local"


def test_private_repo_needs_turn(store, config):
    assert run(store, config, event(private=True))[0] == "private_repo"


def test_own_post_is_dropped_only_with_marker_and_agents_login(store, config):
    own = event("github.pr_comment.created", has_reviewer_marker=True, author="review-bot")
    assert run(store, config, own) == ("own_post", [])
    forged = event("github.pr_comment.created", has_reviewer_marker=True, author="someone")
    assert run(store, config, forged)[0] == "reply_not_for_review"


def test_reply_from_author_after_findings_re_requests_same_head(store, config):
    run(store, config, event())
    run(store, config, worker("worker.result", result=json.dumps({"findings": [{"id": 1}]})))
    reply = event("github.pr_comment.created", author="dev", pr_author_login="dev", comment_id=7)
    reason, lines = run(store, config, reply)
    assert reason == "requested"
    assert facts_of(lines, "qa.review_stage_requested")[0]["attempt"] == 2


def test_reply_from_author_after_a_product_decision_only_re_requests(store, config):
    """A pass with a product decision fails the status; the author's answer must re-review."""
    run(store, config, event())
    run(store, config, worker("worker.result", result=json.dumps({"verdict": "pass", "findings": [], "product_decision": "x"})))
    reply = event("github.pr_comment.created", author="dev", pr_author_login="dev", comment_id=7)
    assert run(store, config, reply)[0] == "requested"


def test_reply_from_author_after_a_clean_pass_is_ignored(store, config):
    run(store, config, event())
    run(store, config, worker("worker.result", result=json.dumps({"verdict": "pass", "findings": []})))
    reply = event("github.pr_comment.created", author="dev", pr_author_login="dev", comment_id=7)
    assert run(store, config, reply) == ("reply_not_for_review", [])


def test_reply_from_unrelated_person_is_ignored(store, config):
    run(store, config, event())
    run(store, config, worker("worker.result", result=json.dumps({"findings": [{"id": 1}]})))
    reply = event("github.pr_comment.created", author="other", pr_author_login="dev", comment_id=7)
    assert run(store, config, reply) == ("reply_not_for_review", [])


def test_reply_during_review_not_in_staging_re_requests_after_result(store, config):
    run(store, config, event())
    reply = event("github.pr_comment.created", author="maintainer", comment_id=9)
    assert run(store, config, reply) == ("already_in_flight", [])
    reason, lines = run(store, config, worker("worker.result", result=json.dumps({"findings": []})))
    assert reason == "reply_after_staging"
    assert types(lines) == ["qa.review_stage_requested", "qa.review_status"]


def test_reply_already_staged_does_not_re_request(store, config):
    run(store, config, event())
    with store.transaction() as state:
        state["staged"][f"{REPO}#12@{HEAD}:1"] = ["9"]
    run(store, config, event("github.pr_comment.created", author="maintainer", comment_id=9))
    assert run(store, config, worker("worker.result", result=json.dumps({"findings": []}))) == ("reviewed", [])


def test_no_result_retries_then_emits_one_terminal_error(store, config):
    config["mode"] = "live"
    run(store, config, event())
    reason, lines = run(store, config, worker("worker.no_result"))
    assert reason == "retry"
    assert facts_of(lines, "qa.review_stage_requested")[0]["attempt"] == 2
    reason, lines = run(store, config, worker("worker.no_result", attempt=2))
    assert reason == "retry_bound"
    assert facts_of(lines, "reviewer.terminal_error") == [
        {"repo": REPO, "pr_number": 12, "head_sha": HEAD, "reason": "no_verdict_after_retries"}]
    assert "gh run rerun" not in "".join(lines)


def test_shadow_brings_a_terminal_error_to_qg_not_the_owner(store, config):
    assert config["mode"] == "shadow"
    run(store, config, event())
    run(store, config, worker("worker.no_result"))
    reason, lines = run(store, config, worker("worker.no_result", attempt=2))
    assert reason == "retry_bound" and not [t for t in types(lines) if t.startswith("reviewer.")]
    assert facts_of(lines, "qa.review_needs_turn") == [
        {"reason": "terminal_no_verdict_after_retries", "repo": REPO, "pr": 12, "head_sha": HEAD}]


def test_result_for_superseded_head_is_ignored(store, config):
    run(store, config, event())
    run(store, config, event(head_sha=NEWER))
    assert run(store, config, worker("worker.result", result="{}")) == ("superseded", [])


def test_an_over_cap_result_retries_under_a_new_identity_then_ends_visibly(store, config):
    """Settle refuses a result over 49,152 bytes as no_result/invalid and keeps that identity in use."""
    run(store, config, event())
    over = {"reason": "invalid", "detail": "the result is over 49152 bytes"}
    reason, lines = run(store, config, worker("worker.no_result", **over))
    [again] = facts_of(lines, "qa.review_stage_requested")
    assert reason == "retry" and again["request_identity"] != rule_config.request_identity(REPO, 12, HEAD, 1)
    reason, lines = run(store, config, worker("worker.no_result", attempt=2, **over))
    assert reason == "retry_bound" and facts_of(lines, "qa.review_needs_turn")


def test_terminal_errors_never_use_retired_types(store, config):
    run(store, config, event())
    run(store, config, worker("worker.no_result"))
    _, lines = run(store, config, worker("worker.no_result", attempt=2))
    retired = {"reviewer.errored", "reviewer.checks_unreadable", "reviewer.pr_info_unreadable"}
    assert not retired & set(types(lines))


def test_missing_facts_need_a_turn(store, config):
    reason, lines = run(store, config, {"type": "github.pr.opened"})
    assert reason == "missing_or_unreadable_facts"
    assert types(lines) == ["qa.review_needs_turn"]


def test_main_reads_stdin_and_prints_event_lines(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "rule-set.json"
    loaded = rule_config.load()
    loaded["thread_root"] = str(tmp_path)
    config_path.write_text(json.dumps(loaded), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event())))
    monkeypatch.setattr(gate, "Github", lambda: Github(FakeGH()))
    assert gate.main(["--config", str(config_path)]) == 0
    assert types(capsys.readouterr().out.splitlines()) == ["qa.review_stage_requested", "qa.review_status"]


def test_runs_still_in_progress_read_as_pending_not_unreadable():
    """GitHub sends conclusion and completed_at as null until a run completes."""
    assert gate.combined_check_state([
        {"name": "ci", "status": "completed", "conclusion": "success", "completed_at": "2026-01-01T00:00:00Z"},
        {"name": "slow", "status": "in_progress", "conclusion": None, "started_at": "2026-01-01T00:00:00Z",
         "completed_at": None},
    ]) == "pending"
    assert gate.combined_check_state([{"name": "q", "status": "queued", "conclusion": None, "completed_at": None,
                                       "started_at": None}]) == "pending"
