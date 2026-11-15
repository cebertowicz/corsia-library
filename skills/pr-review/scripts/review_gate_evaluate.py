#!/usr/bin/env python3
"""`evaluate`: the review rule set's fact-only decision (rules 1, 2, 5 and 6a).

The rule runner supplies one event on standard input. This script reads no comment
text. It decides from event facts, local state, and GitHub reads of checks, mergeability, and the matching open PR, then prints event lines. It never writes to GitHub: each status it decides goes to `publish` as
``qa.review_status`` (rule 4b), a review goes to `stage_review` as
``qa.review_stage_requested`` (rule 3a), and the retry bound ends in
``reviewer.terminal_error`` for the PR's owner.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
from dataclasses import dataclass
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any, Iterable, Iterator, Mapping

try:  # Supported hosts are POSIX; do not silently weaken concurrent claims.
    import fcntl
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("review-gate evaluate requires POSIX file locking") from exc

import review_gate_config as rule_config
from review_gate_github import Github, GithubUnavailable

GREEN_STATES = frozenset({"green", "all_green", "success", "skipped", "neutral"})
FAILED_CONCLUSIONS = frozenset({"failure", "failed", "cancelled", "timed_out"})
_REPO_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")
PR_EVENTS = frozenset({"github.pr.opened", "github.pr.synchronize", "github.pr.pushed",
                       "github.pr.ready_for_review", "github.head.checks_completed"})
LABEL_EVENTS = frozenset({"github.pr.labeled", "github.pr.unlabeled"})
END_EVENTS = frozenset({"github.pr.closed", "github.pr.merged"})
# What a PR's end clears; reviewed stays, so a reopened PR's unchanged head isn't reviewed again.
END_CLEARS = ("in_flight", "attempts", "failures", "publish_failures", "mergeability", "replies")
# Dev's connector names first; the design's names (rule 2) after them.
REPLY_EVENTS = frozenset({"github.pr_comment.created", "github.review.submitted",
                          "github.pr.comment.created", "github.pr.review_comment.created",
                          "github.pr.review.submitted"})
OVERRIDE_LABEL = "qa/override-reviewer"


@dataclass(frozen=True)
class Identity:
    repo: str
    pr: int
    head_sha: str


def _identity(facts: Mapping[str, Any]) -> Identity | None:
    repo = facts.get("repo")
    pr = facts.get("pr", facts.get("pr_number"))
    # head_sha on head.checks_completed (the commit the checks ran on), sha on a PR event (its payload head)
    head_sha = facts.get("head_sha", facts.get("sha"))
    if not isinstance(repo, str) or not isinstance(pr, int) or isinstance(pr, bool) or not isinstance(head_sha, str):
        return None
    if not _valid_repo(repo) or pr <= 0 or not _SHA.fullmatch(head_sha):
        return None
    return Identity(repo, pr, head_sha.lower())


def _valid_repo(repo: str) -> bool:
    parts = repo.split("/")
    return len(parts) == 2 and all(_REPO_PART.fullmatch(part) for part in parts)


def _labels(facts: Mapping[str, Any]) -> tuple[str, ...]:
    labels = facts.get("labels", [])
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        return ()
    return tuple(labels)


def _outside(facts: Mapping[str, Any], reply: bool = False) -> bool:
    """An outside commit. A missing side is treated as outside, so it is never reviewed unscreened.

    On a reply, ``author_origin`` is the commenter's, not the PR's: a maintainer's comment on
    an outside PR must not skip the screen, so a reply reads only ``pr_author_origin``."""
    origin = facts.get("pr_author_origin" if reply else "author_origin")
    if isinstance(origin, str):
        return origin == "outside"
    head_repo, base_repo = facts.get("head_repo"), facts.get("base_repo")
    return not isinstance(head_repo, str) or not isinstance(base_repo, str) or head_repo.lower() != base_repo.lower()


def combined_check_state(check_runs: Any, contexts: Iterable[str] = ()) -> str:
    """Reduce the newest run per name, excluding configured review contexts."""
    if not isinstance(check_runs, list):
        return "unreadable"
    newest: dict[str, tuple[tuple[str, int], Mapping[str, Any]]] = {}
    for index, run in enumerate(check_runs):
        if not isinstance(run, Mapping) or not isinstance(run.get("name"), str):
            return "unreadable"
        name = run["name"]
        if name in contexts:
            continue
        timestamp = run.get("completed_at") or run.get("started_at") or ""  # null while a run is in progress
        if not isinstance(timestamp, str):
            return "unreadable"
        candidate = ((timestamp, index), run)
        if name not in newest or candidate[0] > newest[name][0]:
            newest[name] = candidate
    if not newest:
        return "pending"
    states = []
    for _, run in newest.values():
        value = run.get("conclusion") or run.get("state") or run.get("status")  # conclusion is null until it completes
        if not isinstance(value, str):
            return "unreadable"
        states.append(value.lower())
    if "action_required" in states:
        return "action_required"
    if any(state in FAILED_CONCLUSIONS for state in states):
        return "failing"
    if any(state not in {"success", "skipped", "neutral"} for state in states):
        return "pending"
    return "all_green"


# GitHub answers mergeable null while it computes it in the background and asks the caller to read
# again shortly. No later event is due for a head whose checks have all finished, so the re-read
# happens inside this run when mergeability is not yet available.
MERGEABLE_REREAD_S = 4.0


class Reads:
    """The GitHub reads one event may need, never written. Each is made once, except a PR read
    whose mergeable GitHub is still computing."""

    def __init__(self, github: Github | None = None, mergeable_reads: int = 3, contexts: Iterable[str] = ()) -> None:
        self._contexts = set(contexts)
        self._github = github
        self._pulls: dict[tuple[str, int], Any] = {}
        self._mergeable_reads = max(1, mergeable_reads)

    @property
    def github(self) -> Github:
        if self._github is None:
            self._github = Github()
        return self._github

    def pull(self, repo: str, pr: int) -> Any:
        if (repo, pr) not in self._pulls:
            self._pulls[(repo, pr)] = self.github.json(f"repos/{repo}/pulls/{pr}")
        return self._pulls[(repo, pr)]

    def check_state(self, repo: str, head: str) -> str:
        """The head's check state: a held suite is ``action_required``; otherwise the newest
        run per name reduced by ``combined_check_state``. A failed read is ``unreadable``."""
        try:
            suites = self.github.json(f"repos/{repo}/commits/{head}/check-suites?per_page=100")["check_suites"]
            if any(isinstance(s, Mapping) and s.get("conclusion") == "action_required" for s in suites):
                return "action_required"
            runs: list[Any] = []
            page = 1
            while True:
                body = self.github.json(f"repos/{repo}/commits/{head}/check-runs?per_page=100&filter=latest&page={page}")
                batch = body["check_runs"]
                runs.extend(batch)
                if not batch or len(runs) >= int(body.get("total_count", len(runs))):
                    return combined_check_state(runs, self._contexts)
                page += 1
        except (GithubUnavailable, KeyError, TypeError, ValueError, AttributeError):
            return "unreadable"

    def mergeable(self, repo: str, pr: int) -> str | None:
        """``MERGEABLE``, ``CONFLICTING`` or ``UNKNOWN`` (GitHub still computing after every read);
        None when unreadable. An unknown or unreadable answer is read again, MERGEABLE_REREAD_S apart."""
        answer: str | None = None
        for attempt in range(self._mergeable_reads):
            if attempt:
                time.sleep(MERGEABLE_REREAD_S)
                self._pulls.pop((repo, pr), None)
            try:
                value = self.pull(repo, pr)["mergeable"]
            except (GithubUnavailable, KeyError, TypeError, ValueError):
                answer = None
                continue
            answer = "UNKNOWN" if value is None else "MERGEABLE" if value is True else "CONFLICTING"
            if answer != "UNKNOWN":
                return answer
        return answer

    def sides(self, repo: str, pr: int) -> dict[str, Any]:
        """The PR's ``head_repo`` and ``base_repo`` from its own read. CORSIA adds ``author_origin``
        to an event only when a rule names that fact, so a checks event usually has neither; an
        unreadable PR or a deleted fork leaves a side missing, which still counts as outside."""
        try:
            pull = self.pull(repo, pr)
            return {"head_repo": ((pull["head"].get("repo") or {}).get("full_name")),
                    "base_repo": pull["base"]["repo"]["full_name"]}
        except (GithubUnavailable, KeyError, TypeError, ValueError, AttributeError):
            return {}

    def pr_for_head(self, repo: str, head: str) -> int | None:
        """The open PR whose head is ``head``. The open-PR list is the base repository's, so it
        holds fork PRs; ``commits/{sha}/pulls`` does not. None when no open PR has that head;
        a failed read raises ``GithubUnavailable``."""
        try:
            pulls = self.github.pages(f"repos/{repo}/pulls?state=open")
        except ValueError as exc:
            raise GithubUnavailable(f"open pull list unreadable: {exc}") from exc
        for pull in pulls:
            if isinstance(pull, Mapping) and str((pull.get("head") or {}).get("sha", "")).lower() == head.lower():
                number = pull.get("number")
                return number if isinstance(number, int) and not isinstance(number, bool) else None
        return None


def _held_suite(facts: Mapping[str, Any]) -> bool:
    suites = facts.get("suite_conclusions")
    return isinstance(suites, list) and "action_required" in suites


def _mode(labels: tuple[str, ...]) -> str:
    # qa/review-parallel is dropped on purpose (the shadow experiment replaced it).
    return "local" if "qa/review-local" in labels else "default"


def _marker(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() in {"yes", "true"})


def _reply_id(facts: Mapping[str, Any]) -> str | None:
    for key in ("comment_id", "review_id", "item_version"):
        value = facts.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
            return str(value)
    return None


def _event_line(event_type: str, facts: Mapping[str, Any]) -> str:
    return json.dumps({"event": {"type": event_type, "facts": facts}}, sort_keys=True)


class StateStore:
    """The owner's JSON state file, read and changed under one exclusive lock."""

    MARKERS = ("reviewed", "in_flight", "attempts", "failures", "publish_failures", "mergeability", "screened",
               "findings", "replies", "staged", "published")

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir.resolve()
        self.path = self.state_dir / "review-gate-state.json"
        self.lock_path = self.state_dir / "review-gate-state.lock"

    @contextmanager
    def transaction(self) -> Iterator[dict[str, Any]]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
                if not isinstance(state, dict):
                    raise ValueError("review-gate state must be a JSON object")
                for marker in self.MARKERS:
                    if not isinstance(state.get(marker), dict):
                        state[marker] = {}
                before = json.dumps(state, sort_keys=True)
                yield state
                if json.dumps(state, sort_keys=True) != before:
                    self._write(state)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def snapshot(self) -> dict[str, Any]:
        with self.transaction() as state:
            return json.loads(json.dumps(state))

    def _write(self, state: Mapping[str, Any]) -> None:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.state_dir, prefix=".review-gate-", delete=False
        ) as temporary:
            json.dump(state, temporary, sort_keys=True, separators=(",", ":"))
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, self.path)


def get(state: Mapping[str, Any], marker: str, repo: str, pr: int) -> Any:
    values = state.get(marker, {})
    repo_values = values.get(repo, {}) if isinstance(values, Mapping) else {}
    return repo_values.get(str(pr)) if isinstance(repo_values, Mapping) else None


def put(state: dict[str, Any], marker: str, repo: str, pr: int, value: Any) -> None:
    repo_values = state[marker].setdefault(repo, {})
    if value is None:
        repo_values.pop(str(pr), None)
        if not repo_values:
            state[marker].pop(repo, None)
    else:
        repo_values[str(pr)] = value


def _for_head(state: Mapping[str, Any], marker: str, identity: Identity) -> Any:
    """A per-PR record kept as ``{"head": sha, ...}``, only when it is for this head."""
    value = get(state, marker, identity.repo, identity.pr)
    return value if isinstance(value, Mapping) and value.get("head") == identity.head_sha else None


class Emitter:
    """The lines one run prints, in order."""

    def __init__(self, shadow: bool = False) -> None:
        self.lines: list[str] = []
        self.shadow = shadow

    def status(self, identity: Identity, state_name: str, attempt: int = 0, base_ref: str | None = None) -> None:
        facts: dict[str, Any] = {"repo": identity.repo, "pr": identity.pr, "head_sha": identity.head_sha,
                                 "attempt": attempt, "state": state_name}
        if base_ref:
            facts["base_ref"] = base_ref
        self.lines.append(_event_line("qa.review_status", facts))

    def needs_turn(self, reason: str, identity: Identity | None = None, **where: Any) -> None:
        facts: dict[str, Any] = {"reason": reason, **where}
        if identity is not None:
            facts.update(repo=identity.repo, pr=identity.pr, head_sha=identity.head_sha)
        self.lines.append(_event_line("qa.review_needs_turn", facts))

    def terminal(self, identity: Identity, reason: str) -> None:
        if self.shadow:  # owner-facing like publish's verdict, so a shadow run brings it to the owner instead
            self.needs_turn(f"terminal_{reason}", identity)
            return
        self.lines.append(_event_line("reviewer.terminal_error", {
            "repo": identity.repo, "pr_number": identity.pr, "head_sha": identity.head_sha, "reason": reason}))


def _request(state: dict[str, Any], identity: Identity, mode: str, out: Emitter) -> None:
    """Step 7: claim the head and request one staging, attempt = last attempt + 1."""
    last = _for_head(state, "attempts", identity)
    attempt = (last["n"] if last else 0) + 1
    put(state, "attempts", identity.repo, identity.pr, {"head": identity.head_sha, "n": attempt})
    put(state, "in_flight", identity.repo, identity.pr, identity.head_sha)
    out.lines.append(_event_line("qa.review_stage_requested", {
        "repo": identity.repo, "pr": identity.pr, "head_sha": identity.head_sha, "attempt": attempt,
        "request_identity": rule_config.request_identity(identity.repo, identity.pr, identity.head_sha, attempt),
        "mode": mode}))
    out.status(identity, "requested", attempt)


def _hold_reply(state: dict[str, Any], identity: Identity, facts: Mapping[str, Any], reply: bool) -> str:
    """Step 5: the head is already in flight; keep a reply's id for the re-review after it."""
    reply_id = _reply_id(facts) if reply else None
    if reply_id is not None:
        held = _for_head(state, "replies", identity) or {"head": identity.head_sha, "ids": []}
        if reply_id not in held["ids"]:
            put(state, "replies", identity.repo, identity.pr,
                {"head": identity.head_sha, "ids": [*held["ids"], reply_id]})
    return "already_in_flight"


def _github(event: Mapping[str, Any], facts: Mapping[str, Any], state: dict[str, Any],
            config: Mapping[str, Any], out: Emitter, reads: Reads) -> str:
    """Steps 1 to 7 for a GitHub PR, label or reply event. Returns the reason for tests."""
    event_type = event.get("type")
    repo = facts.get("repo")
    if event_type == "github.head.checks_completed" and facts.get("pr_number", facts.get("pr")) is None:
        head = facts.get("head_sha", facts.get("sha"))
        if not (isinstance(repo, str) and _valid_repo(repo) and isinstance(head, str) and _SHA.fullmatch(head)):
            return "no_pr"
        try:
            found = reads.pr_for_head(repo, head)
        except GithubUnavailable as exc:
            print(f"review-gate evaluate: {exc}", file=sys.stderr)
            out.needs_turn("pr_lookup_failed", repo=repo, head_sha=head.lower())
            return "pr_lookup_failed"
        if found is None:
            return "no_pr"  # a branch push with no PR (dev itself) has nothing to review
        facts = {**facts, "pr": found}
    reply = event_type in REPLY_EVENTS
    if reply and not facts.get("current_head"):
        # A reply has no head of its own: read the PR's live head. A failed read names itself, never drops the reply (W2).
        pr_number = facts.get("pr", facts.get("pr_number"))
        if isinstance(repo, str) and _valid_repo(repo) and isinstance(pr_number, int) and not isinstance(pr_number, bool):
            try:
                facts = {**facts, "current_head": str(reads.pull(repo, pr_number)["head"]["sha"])}
            except (GithubUnavailable, KeyError, TypeError, ValueError):
                out.needs_turn("reply_pr_read_failed", repo=repo, pr=pr_number)
                return "reply_pr_read_failed"
    # A reply has no head of its own (the connector drops the payload's sha): it is about the
    # PR's live head, which the connector's read puts on current_head.
    identity = _identity({**facts, "head_sha": facts["current_head"]} if reply and facts.get("current_head") else facts)
    if identity is None:
        out.needs_turn("missing_or_invalid_identity")
        return "missing_or_invalid_identity"
    # step 1: an event a rule reads for carries the PR's live head as current_head; it is stale
    # when the event's own head differs.
    live = facts.get("current_head")
    if isinstance(live, str) and live.lower() != identity.head_sha:
        return "stale_head"
    if reply and _marker(facts.get("has_reviewer_marker")) and facts.get("author") == config.get("agents_login"):
        return "own_post"  # step 2
    if facts.get("private") is True or facts.get("repository_visibility") == "private":
        out.needs_turn("private_repo", identity)
        return "private_repo"
    labels = _labels(facts)
    if OVERRIDE_LABEL in labels:  # step 2a
        out.status(identity, "skipped")
        return "override_skip"
    if event_type in LABEL_EVENTS:  # a label never starts a review (W3)
        return "label_only"
    checks = "action_required" if _held_suite(facts) else reads.check_state(identity.repo, identity.head_sha)  # step 3
    if checks == "action_required":
        out.status(identity, "maintainer_checks")
        return "checks_action_required"
    if checks == "unreadable":
        if get(state, "in_flight", identity.repo, identity.pr) == identity.head_sha:
            # Its checks were read green when it was requested; a later failed read changes nothing.
            return _hold_reply(state, identity, facts, reply)
        out.needs_turn("checks_unreadable", identity)
        return "checks_unreadable"
    if facts.get("draft") is True:
        out.status(identity, "waiting_checks")
        return "draft"
    mergeable = reads.mergeable(identity.repo, identity.pr)
    if not isinstance(mergeable, str) or mergeable.upper() == "UNKNOWN":
        if get(state, "in_flight", identity.repo, identity.pr) == identity.head_sha:
            # It was read mergeable when it was requested; an unknown read now must not post
            # 'Waiting for checks' over 'Review requested' or raise a terminal for a live review.
            return _hold_reply(state, identity, facts, reply)
        if checks not in GREEN_STATES:  # the checks' own completion event reads it again
            out.status(identity, "checks_failed" if checks == "failing" else "waiting_checks")
            return f"checks_{checks}"
        # Green, and Reads.mergeable already read it again within this run: no later event is due,
        # so raise it now, once per head, rather than leave a status nothing revisits.
        seen = _for_head(state, "mergeability", identity)
        if not (seen and seen.get("raised")):
            put(state, "mergeability", identity.repo, identity.pr, {"head": identity.head_sha, "raised": True})
            out.terminal(identity, "mergeability_unresolved")
        return "mergeability_unresolved"
    if mergeable.upper() != "MERGEABLE":  # G1: a conflicting PR waits, and says on what
        branch = facts.get("branch")  # the connector's base ref
        out.status(identity, "conflicts", base_ref=branch if isinstance(branch, str) else None)
        return "not_mergeable"
    if checks not in GREEN_STATES:  # a failed check is named, so it doesn't read as still running
        out.status(identity, "checks_failed" if checks == "failing" else "waiting_checks")
        return f"checks_{checks}"
    if not isinstance(facts.get("pr_author_origin" if reply else "author_origin"), str) \
            and not (isinstance(facts.get("head_repo"), str) and isinstance(facts.get("base_repo"), str)):
        facts = {**facts, **reads.sides(identity.repo, identity.pr)}
    if _outside(facts, reply):  # step 4
        screen = _for_head(state, "screened", identity)
        if screen is None:
            out.status(identity, "safety_check")
            return "screen_pending"
        if screen.get("verdict") != "safe":
            out.status(identity, "held")
            return "screen_held"
    if get(state, "in_flight", identity.repo, identity.pr) == identity.head_sha:  # step 5
        return _hold_reply(state, identity, facts, reply)
    if reply:  # step 6
        author = facts.get("author")
        findings = _for_head(state, "findings", identity)
        by_author = author is not None and author == facts.get("pr_author_login") and bool(findings and findings.get("count"))
        by_owner = author is not None and author == config.get("sme")
        if not (by_author or by_owner):
            return "reply_not_for_review"
    elif get(state, "reviewed", identity.repo, identity.pr) == identity.head_sha:
        return "already_reviewed"
    _request(state, identity, _mode(labels), out)  # step 7
    return "requested"


def _ended(facts: Mapping[str, Any], state: dict[str, Any]) -> str:
    """A closed or merged PR: drop its in-flight claim and counters, since no result rule runs once it ends."""
    repo, pr = facts.get("repo"), facts.get("pr_number", facts.get("pr"))
    if not isinstance(repo, str) or not _valid_repo(repo) or not isinstance(pr, int) or isinstance(pr, bool) or pr <= 0:
        return "missing_or_invalid_identity"
    for marker in END_CLEARS:
        put(state, marker, repo, pr, None)
    return "pr_ended"


def _worker(event: Mapping[str, Any], facts: Mapping[str, Any], state: dict[str, Any],
            config: Mapping[str, Any], out: Emitter) -> str:
    """Rules 5 and 6a: a review worker's or the screen worker's settled result."""
    workflow = str(facts.get("workflow") or "")
    result = event.get("type") == "worker.result"
    # A refused start carries the bare workflow name, a settled one name@version.
    if workflow.partition("@")[0] == str(config["screen_workflow"]):
        identity = _identity({"repo": facts.get("repo"), "pr": facts.get("pr_number", facts.get("pr")),
                              "head_sha": facts.get("item_version")})
        if identity is None:
            out.needs_turn("screen_result_unreadable")
            return "screen_result_unreadable"
        verdict = None
        if result and facts.get("valid") is True:
            with contextlib.suppress(TypeError, ValueError, AttributeError):
                data = json.loads(facts.get("result") or "{}")  # settle unwraps a declared payload
                verdict = data.get("payload", data).get("verdict")
        put(state, "screened", identity.repo, identity.pr, {"head": identity.head_sha, "verdict": verdict or "flagged"})
        if verdict != "safe":  # flagged, invalid or no result all hold (O4)
            out.status(identity, "held")
            return "screen_held"
        return "screen_safe"
    parsed = rule_config.parse_identity(facts.get("request_identity"))
    if parsed is None:
        out.needs_turn("worker_identity_unreadable")
        return "worker_identity_unreadable"
    repo, pr, head, attempt = parsed
    identity = Identity(repo, pr, head)
    current = get(state, "in_flight", repo, pr)
    if current not in (None, head):
        return "superseded"  # a newer commit is already in review
    put(state, "in_flight", repo, pr, None)
    if result:
        put(state, "reviewed", repo, pr, head)
        put(state, "failures", repo, pr, None)
        count = 0
        with contextlib.suppress(TypeError, ValueError, AttributeError):
            parsed = json.loads(facts.get("result") or "{}")
            # A product decision is an open item the author can answer, like a finding.
            count = len(parsed.get("findings") or []) + (1 if parsed.get("product_decision") else 0)
        put(state, "findings", repo, pr, {"head": head, "count": count})
        staged = set(state["staged"].pop(facts.get("request_identity"), []) or [])
        replies = _for_head(state, "replies", identity)
        if replies and any(reply_id not in staged for reply_id in replies["ids"]):
            put(state, "replies", repo, pr, None)
            _request(state, identity, "default", out)  # W2: a reply arrived after staging
            return "reply_after_staging"
        return "reviewed"
    state["staged"].pop(facts.get("request_identity"), None)
    return retry(state, identity, config, out)


def retry(state: dict[str, Any], identity: Identity, config: Mapping[str, Any], out: Emitter) -> str:
    """Count one failed attempt on the head and request it again, or end it at the retry bound."""
    failures = _for_head(state, "failures", identity)
    count = (failures["n"] if failures else 0) + 1
    put(state, "failures", identity.repo, identity.pr, {"head": identity.head_sha, "n": count})
    if count >= int(config["no_result_retry_bound"]):
        out.terminal(identity, "no_verdict_after_retries")
        return "retry_bound"
    _request(state, identity, "default", out)
    return "retry"


def handle(event: Mapping[str, Any], store: StateStore, config: Mapping[str, Any],
           github: Github | None = None) -> tuple[str, list[str]]:
    """Decide one event and apply its state change atomically; the reason and lines to print."""
    out = Emitter(config.get("mode") == "shadow")
    reads = Reads(github, int(config.get("mergeability_reread_bound", 3)), rule_config.all_contexts(config))
    facts = event.get("facts")
    if not isinstance(facts, Mapping):
        out.needs_turn("missing_or_unreadable_facts")
        return "missing_or_unreadable_facts", out.lines
    event_type = event.get("type")
    with store.transaction() as state:
        if event_type in END_EVENTS:
            reason = _ended(facts, state)
        elif event_type in PR_EVENTS or event_type in LABEL_EVENTS or event_type in REPLY_EVENTS:
            reason = _github(event, facts, state, config, out, reads)
        elif event_type in {"worker.result", "worker.no_result"}:
            reason = _worker(event, facts, state, config, out)
        else:
            reason = "not_an_evaluate_event"
    return reason, out.lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="rule-set.json; defaults to the pack's")
    parser.add_argument("--event", type=Path, help="JSON event file; otherwise read one event from stdin")
    args = parser.parse_args(argv)
    try:
        config = rule_config.load(args.config)
        if args.event is None:
            event = json.load(sys.stdin)
        else:
            with args.event.open(encoding="utf-8") as event_file:
                event = json.load(event_file)
    except (OSError, ValueError) as exc:
        print(f"review-gate evaluate: invalid input: {exc}", file=sys.stderr)
        return 2
    if not isinstance(event, Mapping):
        print("review-gate evaluate: stdin event must be a JSON object", file=sys.stderr)
        return 2
    _, lines = handle(event, StateStore(rule_config.folder(config, "state")), config, Github())
    for line in lines:
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(rule_config.timed("evaluate", main))
