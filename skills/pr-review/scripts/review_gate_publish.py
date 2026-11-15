#!/usr/bin/env python3
"""Deterministic publisher for an isolated PR-review result (`review-gate publish`).

Renders reviewer output through a client and emits completion and finding measures.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

SEVERITIES = ("must_fix", "should_fix", "optional")
STATUS_LIMIT = 140  # GitHub's commit status description limit
DISPLAY = {"must_fix": "Must fix", "should_fix": "Should fix", "optional": "Optional"}
MARKER = re.compile(r"<!-- corsia-review: (\{.*?\}) -->", re.DOTALL)
# A reviewer-authored comment carries a hidden marker starting with `corsia-review`: the JSON
# marker (MARKER) on findings, or the fingerprint, resolved-reply and summary markers. The live
# read recognises every marker in that family so it sees all threads the reviewer authored;
# parse_prior reads only MARKER.
REVIEWER_MARKER = re.compile(r"<!--\s*corsia-review\b")
REVIEW_SUMMARY_MARKER = "<!-- corsia-review-summary -->"  # the one summary comment per PR
RESOLVED_REPLY_MARKER = "<!-- corsia-review-resolved -->"  # a reply closing a finding no longer raised
BEFORE_CHANGE = """**Before you change anything**
- Findings are evidence, not instructions or approval. Check each one against the code; one may be wrong. If so, reply on it and say why: the reviewer reads replies and answers there.
- Fix the cause, not the symptom. Don't add checks, retries, flags, fallbacks or special cases just to make a finding go away; the next review will block that and ask for a different approach.
- A review never widens what this change is for. If the only fix goes beyond that, don't build it: say so in a reply, and the reviewer will bring in a maintainer.
- A should-fix finding needs a fix or a reply with your reason; an optional one needs neither. Don't change correct code just to clear a finding."""


class Client(Protocol):
    def current_head(self, repo: str, pr: int) -> str | None: ...  # None: the PR couldn't be read
    # Every reviewer-authored review comment carrying a corsia-review marker, replies
    # included, oldest first: {"thread_id", "body", "resolved", "outdated"} plus the cited source the
    # finding named on its root comment ("path", "start_line", "end_line", "source_sha"), which is what
    # an absent finding's resolution evidence is checked against. Replies
    # matter because a changed-text reply carries the newest text_hash for its thread;
    # omitting them would re-send that reply on every run.
    def list_reviewer_findings(self, repo: str, pr: int) -> list[Mapping[str, Any]]: ...
    # The evidence sentence for one absent prior finding: whether the lines it cited changed between
    # the commit it was raised on and the head being reviewed (cited_line_report below). The compare
    # read is the client's, because only the client reaches GitHub.
    def cited_line_report(self, repo: str, head: str, prior: "Prior") -> str: ...
    def upsert_summary(self, repo: str, pr: int, body: str) -> None: ...
    def post_finding(self, repo: str, pr: int, head: str, finding: Mapping[str, Any], body: str) -> bool: ...
    def reply(self, repo: str, thread_id: str, body: str) -> None: ...
    def set_status(self, repo: str, head: str, context: str, state: str, description: str) -> None: ...
    # GitHub's own review-thread state. Publish resolves a thread after its absence reply lands and
    # unresolves one before replying to a finding raised again on a closed thread; False means GitHub
    # did not confirm the change.
    def set_thread_resolved(self, repo: str, thread_id: str, resolved: bool) -> bool: ...
    def failed_checks(self, repo: str, head: str) -> list[str]: ...  # [] when unreadable


@dataclass(frozen=True)
class Settings:
    model: str
    show_model_in_output: bool = False
    sme: str | None = None
    rules: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    standards: tuple[str, ...] = ()
    verdict_context: str = "llm-review"
    presence_context: str | None = "llm-review-present"  # shadow mode has none


@dataclass(frozen=True)
class Prior:
    thread_id: str
    fingerprint: str
    severity: str
    text_hash: str | None
    resolved: bool
    outdated: bool
    # What the finding's own root comment cited, as GitHub recorded it: the file, the original line
    # range and the commit it was raised on. Empty/None when the comment carried no citable position.
    path: str = ""
    start_line: int | None = None
    end_line: int | None = None
    source_sha: str = ""


@dataclass(frozen=True)
class PublishResult:
    published: bool
    reason: str
    events: tuple[dict[str, Any], ...]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and 7 <= len(value) <= 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def _valid_finding(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("severity") in SEVERITIES and all(isinstance(value.get(key), str) and value[key].strip() for key in ("category", "text", "fingerprint")) and isinstance(value.get("path", ""), str) and isinstance(value.get("line", 0), int) and value.get("line", 0) >= 0


def validate_envelope(envelope: Any) -> tuple[bool, str]:
    if not isinstance(envelope, Mapping) or envelope.get("source") != "outside" or envelope.get("kind") != "qa.review" or envelope.get("valid") is not True:
        return False, "invalid_envelope"
    if not isinstance(envelope.get("repo"), str) or not isinstance(envelope.get("pr"), int) or envelope["pr"] <= 0 or not _is_sha(envelope.get("head_sha")):
        return False, "invalid_envelope"
    if any(not isinstance(envelope.get(key), str) or not envelope[key] for key in ("run_id", "workflow")):
        return False, "invalid_envelope"
    payload = envelope.get("payload")
    # A refused start never ran a version, so only its error verdict may have none.
    if not isinstance(envelope.get("workflow_version"), str) or not (envelope["workflow_version"] or (isinstance(payload, Mapping) and payload.get("verdict") == "error")):
        return False, "invalid_envelope"
    if not isinstance(payload, Mapping) or payload.get("verdict") not in {"pass", "findings", "stop", "error"} or not isinstance(payload.get("findings", []), list):
        return False, "invalid_payload"
    if not all(_valid_finding(item) for item in payload["findings"]):
        return False, "invalid_payload"
    if payload["verdict"] == "pass" and payload["findings"]:
        return False, "invalid_payload"
    return True, "ok"


def parse_prior(comment: Mapping[str, Any]) -> Prior | None:
    body = comment.get("body")
    match = MARKER.search(body) if isinstance(body, str) else None
    if not match or not isinstance(comment.get("thread_id"), str):
        return None
    try:
        marker = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(marker, Mapping) or not isinstance(marker.get("fingerprint"), str) or marker.get("severity") not in SEVERITIES:
        return None
    text_hash = marker.get("text_hash")
    # The Client contract hands back the cited position already read off the finding's root comment;
    # a recorded or dry-run row may still be the raw comment, whose own keys are the fallback.
    end_line = comment.get("end_line")
    if end_line is None:
        end_line = comment.get("original_line") if comment.get("original_line") is not None else comment.get("line")
    start_line = comment.get("start_line")
    if start_line is None:
        start_line = end_line
    source_sha = comment.get("source_sha") or comment.get("original_commit_id") or comment.get("commit_id") or ""
    return Prior(comment["thread_id"], marker["fingerprint"], marker["severity"], text_hash if isinstance(text_hash, str) else None, bool(comment.get("resolved")), bool(comment.get("outdated")),
                 str(comment.get("path") or ""), start_line if isinstance(start_line, int) and not isinstance(start_line, bool) else None,
                 end_line if isinstance(end_line, int) and not isinstance(end_line, bool) else None, str(source_sha))


def select_prior(items: Iterable[Prior]) -> dict[str, Prior]:
    """One prior per fingerprint: the newest open thread, else the newest closed one."""
    chosen: dict[str, Prior] = {}
    for item in items:  # oldest first, per the Client contract
        old = chosen.get(item.fingerprint)
        if old is None or (old.resolved or old.outdated) or not (item.resolved or item.outdated):
            chosen[item.fingerprint] = item
    return chosen


# --- the resolution evidence for a finding that was not raised again ---------------------
# Report evidence for a finding that is no longer raised.

_SHA_RE = re.compile(r"[0-9a-f]{7,40}\Z")
_HUNK_RE = re.compile(r"^@@ -(?P<start>\d+)(?:,(?P<count>\d+))? ")


def build_resolve_reply_body(report: str) -> str:
    """Build an honest reply for a finding absent from the current review run."""
    return f"Not raised this run. {report}\n\n{RESOLVED_REPLY_MARKER}"


def _valid_sha(value: Any) -> bool:
    """Return whether a value can safely identify an exact Git commit."""
    return isinstance(value, str) and bool(_SHA_RE.fullmatch(value))


def _matching_changed_file(comparison: Mapping[str, Any] | None, path: str) -> Mapping[str, Any] | None:
    """Return the changed file record for the original cited path, if present."""
    if comparison is None:
        return None
    for changed_file in comparison["files"]:
        if not isinstance(changed_file, Mapping):
            continue
        if path in {changed_file.get("filename"), changed_file.get("previous_filename")}:
            return changed_file
    return None


def _cited_range_changed(patch: str, start_line: int, end_line: int) -> bool:
    """Return whether a unified-diff hunk changes the cited original range."""
    source_line: int | None = None
    for patch_line in patch.splitlines():
        match = _HUNK_RE.match(patch_line)
        if match:
            source_line = int(match.group("start"))
            continue
        if source_line is None:
            continue
        if patch_line.startswith("-"):
            if start_line <= source_line <= end_line:
                return True
            source_line += 1
        elif patch_line.startswith("+"):
            if start_line < source_line <= end_line:
                return True
        elif patch_line.startswith(" "):
            source_line += 1
    return False


def _changed_report(changed: bool, source_sha: str, head_sha: str) -> str:
    """Render the exact-SHA evidence sentence without implying the finding is fixed."""
    state = "changed" if changed else "unchanged"
    return (
        f"Cited lines {state} since finding commit "
        f"(`{source_sha[:8]}` → `{head_sha[:8]}`); not verified fixed."
    )


def cited_line_report(prior: Prior, head_sha: str, compare: Callable[[str, str], tuple[Mapping[str, Any] | None, str]]) -> str:
    """Describe whether the finding's own cited source lines changed at exact SHAs.

    ``compare(source_sha, head_sha)`` is the client's GitHub comparison, returning its file inventory
    or the reason it could not be read. The 300-file comparison limit prevents an unsupported claim."""
    path, start_line, end_line, source_sha = prior.path, prior.start_line, prior.end_line, prior.source_sha
    if (not isinstance(path, str) or not path or not isinstance(start_line, int) or isinstance(start_line, bool)
            or not isinstance(end_line, int) or isinstance(end_line, bool)
            or start_line < 1 or end_line < start_line):
        return "No cited lines; not verified fixed."
    if not _valid_sha(source_sha) or not _valid_sha(head_sha):
        return "Could not check cited lines: finding commit is unavailable; not verified fixed."
    if source_sha == head_sha:
        return _changed_report(False, source_sha, head_sha)

    data, error = compare(source_sha, head_sha)
    if error or data is None:
        return f"Could not check cited lines: {error}; not verified fixed."
    changed_file = _matching_changed_file(data, path)
    if changed_file is None:
        if len(data["files"]) >= 300:
            return "Could not check cited lines: comparison may omit files; not verified fixed."
        return _changed_report(False, source_sha, head_sha)
    patch = changed_file.get("patch")
    if not isinstance(patch, str):
        return "Could not check cited lines: comparison omitted the file patch; not verified fixed."
    return _changed_report(_cited_range_changed(patch, start_line, end_line), source_sha, head_sha)


def _compare_inventory(data: Any) -> tuple[Mapping[str, Any] | None, str]:
    """Validate a comparison response."""
    if not isinstance(data, Mapping) or not isinstance(data.get("files"), list):
        return None, "GitHub compare returned no file inventory"
    if data.get("status") not in {"ahead", "identical"}:
        return None, "finding commit does not precede reviewed head"
    return data, ""


def _counts(findings: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    result = {severity: 0 for severity in SEVERITIES}
    for finding in findings:
        result[str(finding["severity"])] += 1
    return result


def _model_suffix(settings: Settings) -> str:
    return f" · {settings.model}" if settings.show_model_in_output else ""


def render_tally(rows: Mapping[str, Mapping[str, int]], *, first_review: bool = False) -> str:
    if first_review:
        return " · ".join(f"{DISPLAY[severity]}: {rows[severity]['Open now']}" for severity in SEVERITIES if rows[severity]["Open now"])
    active = [severity for severity in SEVERITIES if any(rows[severity].values())]
    columns = [name for name in ("Open now", "New", "Still open", "Fixed", "Dropped") if any(rows[severity][name] for severity in active)]
    for severity in active:
        assert rows[severity]["Open now"] == rows[severity]["New"] + rows[severity]["Still open"]
    header = "|            | " + " | ".join(columns) + " |"
    divider = "|------------|" + "|".join("-" * (len(name) + 2) for name in columns) + "|"
    lines = ["| " + DISPLAY[severity] + " | " + " | ".join(str(rows[severity][name]) if rows[severity][name] else "–" for name in columns) + " |" for severity in active]
    return "Since the last review:\n\n" + "\n".join([header, divider, *lines])


def _heading(payload: Mapping[str, Any], counts: Mapping[str, int]) -> str:
    # Same order as status_line: a product decision fails the status even on a pass verdict.
    if payload.get("product_decision"): return "### Review: needs a product decision before this can merge"
    if payload["verdict"] == "pass": return "### Review: no issues found"
    if payload["verdict"] == "stop": return "### Review: this needs a different approach"
    if counts["must_fix"]:
        return f"### Review: {counts['must_fix']} must-fix {'item' if counts['must_fix'] == 1 else 'items'} before this can merge"
    return f"### Review: nothing blocking, {counts['should_fix']} should-fix {'item' if counts['should_fix'] == 1 else 'items'}"


def render_summary(envelope: Mapping[str, Any], settings: Settings, rows: Mapping[str, Mapping[str, int]], *, first_review: bool, fallbacks: Iterable[Mapping[str, Any]] = ()) -> str:
    payload, findings = envelope["payload"], list(envelope["payload"]["findings"])
    lines = [_heading(payload, _counts(findings)), ""]
    if payload.get("product_decision"):
        lines.extend([f"{payload['product_decision']} If you didn't mean to change this, undo it and push.", ""])
    if payload["verdict"] == "stop":
        cause = str(payload.get("shared_cause", ""))
        if cause: lines.extend([f"The findings share one cause: {cause} Fix that cause, not each finding on its own.", ""])
        lines.extend(["Rework the change around that cause before you push again. If you think the cause is wrong, or that your current approach is right, reply on this PR and say why. The reviewer reads your reply and answers it. If you still disagree, say so, and the reviewer asks a maintainer to decide. A push with neither the rework nor a reply will get this same result.", ""])
    if findings: lines.extend([BEFORE_CHANGE, "", render_tally(rows, first_review=first_review), ""])
    fallback_list = list(fallbacks)
    if fallback_list:
        lines.extend(["**Findings not attached to a changed line**", ""])
        for finding in fallback_list: lines.extend([render_finding(finding, settings), ""])
    suffix = f" by {settings.model}" if settings.show_model_in_output else ""
    lines.append(f"Automated review of commit {envelope['head_sha'][:7]}{suffix}.")
    return "\n".join(lines)


def render_finding(finding: Mapping[str, Any], settings: Settings) -> str:
    family, separator, detail = str(finding["category"]).replace("_", " ").replace("-", " ").partition("/")
    category = family.capitalize() + (f": {detail.lower()}" if separator else "")
    body = [f"**{DISPLAY[str(finding['severity'])]}** · {category}", "", str(finding["text"])]
    rule_id = finding.get("rule_id")
    if isinstance(rule_id, str) and rule_id in settings.rules:
        rule, base_sha = settings.rules[rule_id], finding.get("base_sha")
        if isinstance(rule.get("text"), str) and isinstance(rule.get("file"), str) and isinstance(base_sha, str):
            matching = [path for path in settings.standards if Path(path).name == Path(rule["file"]).name]
            rule_path = matching[0] if len(matching) == 1 else rule["file"]
            body.extend(["", f'Rule [{rule_id}](<https://github.com/{finding.get("repo", "")}/blob/{base_sha}/{rule_path}#{rule_id.lower()}>): "{rule["text"]}"'])
    marker = {"severity": finding["severity"], "category": finding["category"], "fingerprint": finding["fingerprint"], "head_sha": finding["head_sha"], "text_hash": _hash(str(finding["text"])), "model": settings.model}
    # The fp line opens a line so CORSIA's has_reviewer_marker event fact is true on this post;
    # without it evaluate reads the post as the PR author's reply and
    # re-requests the same head, creating a loop.
    body.extend(["", f"<!-- corsia-review: {json.dumps(marker, sort_keys=True)} -->",
                 f"<!-- corsia-review-fp:{finding['fingerprint']} -->"])
    return "\n".join(body)


def status_for_state(state: str, settings: Settings, *, run_id: str = "", base_ref: str = "", failed: Iterable[str] = ()) -> tuple[str, str]:
    if state == "conflicts":
        return "pending", f"Waiting for conflicts with {base_ref or 'the base branch'} to be resolved"
    if state == "checks_failed":
        tail = " · review starts when checks pass"
        names = ", ".join(failed)
        if len(names) > STATUS_LIMIT - len("Failed: ") - len(tail):
            names = names[:STATUS_LIMIT - len("Failed: ") - len(tail) - 1] + "…"
        return "pending", (f"Failed: {names}" if names else "A check failed") + tail
    table = {"safety_check": ("pending", "Checking the change before review"), "held": ("pending", "Held for a maintainer"), "waiting_checks": ("pending", "Waiting for checks · review starts when they pass"), "maintainer_checks": ("pending", "Waiting for a maintainer to run the checks"), "requested": ("pending", "Review requested"), "skipped": ("success", "Review skipped by a maintainer")}
    return ("error", "Review couldn't finish · retrying · ref " + run_id[:8]) if state == "error" else table[state]


def status_line(payload: Mapping[str, Any], settings: Settings) -> tuple[str, str]:
    if payload["verdict"] == "error": return status_for_state("error", settings, run_id=str(payload.get("run_id", "")))
    if payload.get("product_decision"): return "failure", "Needs a product decision" + _model_suffix(settings)
    if payload["verdict"] == "pass": return "success", "No issues found" + _model_suffix(settings)
    counts = _counts(payload["findings"])
    if payload["verdict"] == "stop": return "failure", f"Different approach needed, {counts['must_fix']} must fix" + _model_suffix(settings)
    if counts["must_fix"]:
        return "failure", f"{counts['must_fix']} must fix" + (f", {counts['should_fix']} should fix" if counts["should_fix"] else "") + _model_suffix(settings)
    return "success", f"Nothing blocking, {counts['should_fix']} should fix" + _model_suffix(settings)


def _completed(envelope: Mapping[str, Any], verdict: str | None = None) -> dict[str, Any]:
    counts = _counts(envelope["payload"]["findings"])
    event = {"type": "qa.review.completed", "pr": envelope["pr"], "head_sha": envelope["head_sha"], "run_id": envelope["run_id"], "model": envelope.get("model", ""), "reviewer_version": envelope["workflow_version"] or None, "verdict": verdict or envelope["payload"]["verdict"], "blocks": counts["must_fix"], "flags": counts["should_fix"], "dedupe": [envelope["pr"], envelope["head_sha"], envelope["run_id"]]}
    if envelope.get("worker_thread"): event["worker_thread"] = envelope["worker_thread"]  # prices the run (review_cost_per_pr)
    return event


def _finding_event(envelope: Mapping[str, Any], finding: Mapping[str, Any], known: Iterable[str]) -> dict[str, Any]:
    rule_id = finding.get("rule_id") if finding.get("rule_id") in set(known) else None
    return {"type": "qa.review.finding", "pr": envelope["pr"], "head_sha": envelope["head_sha"], "run_id": envelope["run_id"], "finding": finding["fingerprint"], "severity": finding["severity"], "category": finding["category"], "rule_id": rule_id, "dedupe": [envelope["run_id"], finding["fingerprint"]]}


def _rows(current: list[dict[str, Any]], prior: Mapping[str, Prior], replies: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    rows = {severity: {name: 0 for name in ("Open now", "New", "Still open", "Fixed", "Dropped")} for severity in SEVERITIES}
    current_fps = {item["fingerprint"] for item in current}
    dropped = {item.get("fingerprint") for item in replies if item.get("outcome") == "dropped"}
    for item in current:
        row = rows[item["severity"]]; row["Open now"] += 1; row["Still open" if item["fingerprint"] in prior else "New"] += 1
    for fingerprint, old in prior.items():
        if fingerprint not in current_fps:
            rows[old.severity]["Dropped" if fingerprint in dropped else "Fixed"] += 1
    return rows


def _addressed(items: Iterable[Mapping[str, Any]], listed: Iterable[Mapping[str, Any]], prior: Mapping[str, Prior]) -> list[dict[str, Any]]:
    """The worker's replies with ``thread_id`` set to the review thread GitHub can address. The worker
    reads the staged conversation, which names comments (REST id or node id), never threads, so it
    may cite a thread id, any comment id on that thread, or only the finding's fingerprint. A reply
    naming no thread on this PR is left out rather than failing the whole verdict; the prior it was
    about then gets the scripted cited-line reply instead."""
    threads: dict[str, str] = {}
    for row in listed:
        thread = row.get("thread_id")
        if not isinstance(thread, str): continue
        threads[thread] = thread
        for ref in row.get("refs") or ():
            threads.setdefault(str(ref), thread)
    out: list[dict[str, Any]] = []
    for item in items:
        ref = str(item.get("thread_id") or "").strip().lstrip("#")
        old = prior.get(item.get("fingerprint")) if isinstance(item.get("fingerprint"), str) else None
        thread = threads.get(ref) or (old.thread_id if old else None)
        if thread is None:
            print(f"reply left out: {ref!r} names no review thread on this PR", file=sys.stderr)
            continue
        out.append({**item, "thread_id": thread})
    return out


def _error(envelope: Mapping[str, Any], settings: Settings, client: Client, reason: str,
           retrying: bool = True) -> PublishResult:
    repo, pr, head = envelope["repo"], envelope["pr"], envelope["head_sha"]
    detail = envelope["payload"].get("error")
    # The error status description names why the review failed (for example, the worker's model hit a
    # usage limit), so the reason goes there, before the run ref: STATUS_LIMIT cuts from the tail, so a
    # long reason must not push the ref out and take the reason with it.
    why = f"{reason}: {detail}" if detail else reason
    again = " · retrying" if retrying else ""
    description = f"Review couldn't finish{again} · {why} · ref {str(envelope['run_id'])[:8]}"[:STATUS_LIMIT]
    try: client.set_status(repo, head, settings.verdict_context, "error", description)
    except Exception: pass
    return PublishResult(False, reason, (_completed(envelope, "error"),))


def publish(envelope: Any, settings: Settings, client: Client, retrying: bool = True) -> PublishResult:
    """Post one settled review. ``retrying`` says whether a failure here is requested again,
    so the error status never claims a retry that won't happen."""
    valid, reason = validate_envelope(envelope)
    if not valid:
        return PublishResult(False, reason, ())
    assert isinstance(envelope, Mapping)
    repo, pr, head = envelope["repo"], envelope["pr"], envelope["head_sha"]
    if _moved(client, repo, pr, head): return PublishResult(False, "superseded", ({"type": "qa.review.skipped", "pr": pr, "head_sha": head, "reason": "superseded"},))
    if envelope["payload"]["verdict"] == "error": return _error(envelope, settings, client, "worker_error", retrying)
    current = [{**item, "repo": repo, "head_sha": head, "base_sha": envelope["payload"].get("base_sha", "")} for item in envelope["payload"]["findings"]]
    try:
        listed = client.list_reviewer_findings(repo, pr)
        prior = select_prior(item for item in (parse_prior(comment) for comment in listed) if item)
        replies = list(envelope["payload"].get("replies", []))
        rows = _rows(current, prior, replies)
        replies = _addressed(replies, listed, prior)
        fallbacks: list[dict[str, Any]] = []
        for item in current:
            old = prior.get(item["fingerprint"])
            changed = old is None or old.severity != item["severity"] or old.text_hash != _hash(item["text"])
            if old is None or old.outdated:
                if not client.post_finding(repo, pr, head, item, render_finding(item, settings)): fallbacks.append(item)
            elif old.resolved:
                # Raised again on a closed thread whose cited line still stands: reopen the thread
                # and post there, falling back to a fresh comment only when GitHub refuses the reopen.
                if client.set_thread_resolved(repo, old.thread_id, False):
                    client.reply(repo, old.thread_id, render_finding(item, settings))
                elif not client.post_finding(repo, pr, head, item, render_finding(item, settings)):
                    fallbacks.append(item)
            elif changed:
                client.reply(repo, old.thread_id, render_finding(item, settings))
        client.upsert_summary(repo, pr, render_summary(envelope, settings, rows, first_review=not prior, fallbacks=fallbacks))
        for reply in replies:
            if reply.get("outcome") in {"dropped", "still_open"} and isinstance(reply.get("thread_id"), str):
                prefix = "Finding dropped:" if reply["outcome"] == "dropped" else "Still open:"
                # Every reply carries a reviewer marker, so the comment rule does not read it as the author's.
                client.reply(repo, reply["thread_id"], f"{prefix} {reply.get('evidence', '')}\n\n{RESOLVED_REPLY_MARKER}")
                # A finding absent this run closes its thread after the reply lands, as
                # publish_absence_reply does: no reply posted means no resolve, so the thread stays
                # open with no claim on it that the finding is gone.
                if reply["outcome"] == "dropped": client.set_thread_resolved(repo, reply["thread_id"], True)
        # A prior the worker said nothing about is closed on the evidence the reviewer can check itself,
        # as publish_absence_reply does: the cited-line reply first, then the resolve.
        answered = {reply["thread_id"] for reply in replies if isinstance(reply.get("thread_id"), str)}
        raised = {item["fingerprint"] for item in current}
        for fingerprint, old in sorted(prior.items()):
            if fingerprint in raised or old.thread_id in answered: continue
            client.reply(repo, old.thread_id, build_resolve_reply_body(client.cited_line_report(repo, head, old)))
            client.set_thread_resolved(repo, old.thread_id, True)
        dispute = envelope["payload"].get("sme_dispute")
        if isinstance(dispute, Mapping):
            dispute = next(iter(_addressed([dispute], listed, prior)), None)
        if isinstance(dispute, Mapping) and settings.sme:
            client.reply(repo, str(dispute["thread_id"]), f"@{settings.sme}, can you weigh in?\nAuthor: {dispute['author']}\nReview: {dispute['review']}\nThis stays {DISPLAY[dispute['severity']]} until @{settings.sme} answers here.\n\n{RESOLVED_REPLY_MARKER}")
        state, description = status_line({**envelope["payload"], "run_id": envelope["run_id"]}, settings)
        client.set_status(repo, head, settings.verdict_context, state, description)
        if settings.presence_context: client.set_status(repo, head, settings.presence_context, "success", "Review published")
    except Exception as exc:
        return _error(envelope, settings, client, f"publish_failure:{type(exc).__name__}: {exc}", retrying)
    events = tuple([_completed(envelope), *(_finding_event(envelope, item, settings.rules) for item in current)])
    return PublishResult(True, "published", events)


# Attributes each event declares in measures.yaml; run_id and the workflow are runner stamps.
DECLARED = {
    "qa.review.completed": ("pr", "head_sha", "model", "reviewer_version", "verdict", "blocks", "flags", "cost_usd", "worker_thread"),
    "qa.review.skipped": ("pr", "head_sha", "reason"),
    "qa.review.finding": ("pr", "head_sha", "finding", "severity", "category", "rule_id"),
}


def measure_line(event: Mapping[str, Any]) -> str:
    attributes = {key: str(event[key]) if key == "pr" else event[key] for key in DECLARED[event["type"]] if event.get(key) is not None}
    return json.dumps({"measure": {"event.name": event["type"], "attributes": attributes}}, sort_keys=True)


class DryRunClient:
    """Reads a recorded head and prior comments; prints every write to stderr instead of making it."""

    def __init__(self, head: str, prior: list[Mapping[str, Any]]):
        self.head, self.prior = head, prior

    def _write(self, name: str, *args: Any) -> None:
        print(json.dumps({"dry_run": name, "args": list(args)}, default=str), file=sys.stderr)

    def current_head(self, repo: str, pr: int) -> str: return self.head
    def list_reviewer_findings(self, repo: str, pr: int) -> list[Mapping[str, Any]]: return self.prior
    def cited_line_report(self, repo: str, head: str, prior: Any) -> str:
        """A dry run reads nothing, so it cannot check the cited lines and says so instead of inventing
        the evidence a real comparison would have produced."""
        self._write("cited_line_report", repo, head, prior.thread_id)
        return "Could not check cited lines: no comparison in a dry run; not verified fixed."
    def upsert_summary(self, *args: Any) -> None: self._write("upsert_summary", *args)
    def post_finding(self, *args: Any) -> bool: self._write("post_finding", *args); return True
    def reply(self, *args: Any) -> None: self._write("reply", *args)
    def set_status(self, *args: Any) -> None: self._write("set_status", *args)
    def set_thread_resolved(self, *args: Any) -> bool: self._write("set_thread_resolved", *args); return True


# --- Rule-run entry (rules 4 and 4b) ---------------------------------------------------
# A rule-started run gets no arguments: it reads rule-set.json and one event on stdin.


def fingerprint(finding: Mapping[str, Any]) -> str:
    """A finding's identity across heads: file, category and cited rule, never its text."""
    return _hash("|".join(str(finding.get(key) or "") for key in ("path", "category", "rule_id")))


def envelope_from_worker(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """The review envelope for one ``worker.result`` or ``worker.no_result``, or None when
    its request identity can't be read. A missing or invalid result is an ``error`` verdict
    carrying the reason and detail, so the owner notice says why."""
    import review_gate_config as rule_config
    facts = event.get("facts") if isinstance(event.get("facts"), Mapping) else {}
    parsed = rule_config.parse_identity(facts.get("request_identity"))
    if parsed is None:
        return None
    repo, pr, head, attempt = parsed
    _, _, version = str(facts.get("workflow") or "").partition("@")
    why = ": ".join(str(facts[key]) for key in ("reason", "detail") if facts.get(key))
    payload: Any = {"verdict": "error", "findings": [], **({"error": why} if why else {})}
    if event.get("type") == "worker.result" and facts.get("valid") is True:
        try:
            payload = json.loads(facts.get("result") or "null")
        except (TypeError, ValueError):
            pass
    if isinstance(payload, Mapping) and isinstance(payload.get("findings"), list):
        payload = {**payload, "findings": [{**item, "fingerprint": fingerprint(item)} if isinstance(item, Mapping) else item
                                           for item in payload["findings"]]}
    return {"source": "outside", "kind": "qa.review", "valid": True, "repo": repo, "pr": pr, "head_sha": head,
            "attempt": attempt, "run_id": str(facts.get("run_id") or ""), "workflow": str(facts.get("workflow") or "reviewer").partition("@")[0],
            "workflow_version": version, "model": str(facts.get("model") or ""), "payload": payload,
            **({"worker_thread": facts["worker_thread"]} if facts.get("worker_thread") else {})}


def verdict_event(envelope: Mapping[str, Any], config: Mapping[str, Any], settings: Settings) -> dict[str, Any]:
    """``reviewer.verdict`` in today's values; ``summary`` is not declarable (free text).
    ``code_fail`` exactly when the posted status fails, so findings with nothing blocking
    read clean when no blocking finding exists."""
    payload = envelope["payload"]
    counts = _counts(payload["findings"])
    return {"repo": envelope["repo"], "pr_number": envelope["pr"], "head_sha": envelope["head_sha"],
            "verdict": "code_fail" if status_line(payload, settings)[0] == "failure" else "clean",
            "required_action": "approach_reset" if payload["verdict"] == "stop" else "none",
            "block_count": counts["must_fix"], "flag_count": counts["should_fix"], "nit_count": counts["optional"],
            "finding_count": len(payload["findings"]), "model": envelope.get("model") or "unrecorded",
            "reviewer_version": envelope["workflow_version"], "reviewer_standards": config["reviewer_standards"]}


class ShadowClient:
    """Shadow mode: reads the PR through the run's read channel, records every write in
    the owner's shadow record instead of making it, and only ever names the shadow
    verdict context. Prior findings are the ones this record holds for the PR."""

    def __init__(self, github: Any, record_dir: Path, context: str, review_contexts: Iterable[str] = ()):
        self.github, self.record_dir, self.context = github, record_dir, context
        self.review_contexts = set(review_contexts) | {context}

    def _file(self, repo: str, pr: int) -> Path:
        return self.record_dir / f"{repo.replace('/', '__')}__{pr}.jsonl"

    def _write(self, repo: str, pr: int | None, name: str, **fields: Any) -> None:
        path = self._file(repo, pr) if pr is not None else self.record_dir / f"{repo.replace('/', '__')}__repo.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"write": name, **fields}, sort_keys=True, default=str) + "\n")

    def current_head(self, repo: str, pr: int) -> str | None:
        from review_gate_github import GithubUnavailable
        try:
            return str(self.github.json(f"repos/{repo}/pulls/{pr}")["head"]["sha"])
        except (GithubUnavailable, KeyError, TypeError, ValueError) as exc:
            self._write(repo, pr, "head_unreadable", error=str(exc)[:300])
            return None

    def list_reviewer_findings(self, repo: str, pr: int) -> list[Mapping[str, Any]]:
        path = self._file(repo, pr)
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [{"thread_id": f"shadow-{index}", "body": row["body"], "resolved": False, "outdated": False,
                 "path": str(row.get("path") or ""), "start_line": row.get("line"), "end_line": row.get("line"),
                 "source_sha": str(row.get("head") or "")}
                for index, row in enumerate(rows) if row.get("write") == "post_finding"]

    def upsert_summary(self, repo: str, pr: int, body: str) -> None: self._write(repo, pr, "upsert_summary", body=body)
    def post_finding(self, repo: str, pr: int, head: str, finding: Mapping[str, Any], body: str) -> bool:
        self._write(repo, pr, "post_finding", head=head, path=finding.get("path"), line=finding.get("line"), body=body)
        return True
    def reply(self, repo: str, thread_id: str, body: str) -> None: self._write(repo, None, "reply", thread_id=thread_id, body=body)
    def set_status(self, repo: str, head: str, context: str, state: str, description: str) -> None:
        if context != self.context:
            raise ValueError(f"shadow mode never writes status context {context}")
        self._write(repo, None, "set_status", head=head, context=context, state=state, description=description)
    def standing_state(self, repo: str, head: str, context: str) -> str | None:
        """The last state recorded for ``context`` on ``head``; in shadow only this record writes it."""
        path = self.record_dir / f"{repo.replace('/', '__')}__repo.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        states = [row["state"] for row in rows if row.get("write") == "set_status" and row.get("head") == head and row.get("context") == context]
        return states[-1] if states else None
    def set_thread_resolved(self, repo: str, thread_id: str, resolved: bool) -> bool:
        self._write(repo, None, "set_thread_resolved", thread_id=thread_id, resolved=resolved)
        return True
    def cited_line_report(self, repo: str, head: str, prior: Prior) -> str:
        """The same evidence sentence, read through the run's read channel: in shadow the comparison is
        a read, so it runs for real and only the writes are recorded."""
        return cited_line_report(prior, head, lambda source, target: self._compare_commits(repo, source, target))

    def _compare_commits(self, repo: str, source_sha: str, head_sha: str) -> tuple[Mapping[str, Any] | None, str]:
        from review_gate_github import GithubUnavailable
        try:
            data = self.github.json(f"repos/{repo}/compare/{source_sha}...{head_sha}")
        except GithubUnavailable as exc:
            return None, str(exc)[:160]
        except ValueError:
            return None, "GitHub compare returned invalid JSON"
        return _compare_inventory(data)

    def failed_checks(self, repo: str, head: str) -> list[str]:
        """The head's failed check names, newest run per name, for the waiting status; [] when unreadable."""
        import review_gate_evaluate as evaluate
        from review_gate_github import GithubUnavailable
        try:
            runs = self.github.json(f"repos/{repo}/commits/{head}/check-runs?per_page=100&filter=latest")["check_runs"]
            return sorted({str(run["name"]) for run in runs if str(run["name"]) not in self.review_contexts
                           and str(run.get("conclusion") or "").lower() in evaluate.FAILED_CONCLUSIONS})
        except (GithubUnavailable, KeyError, TypeError, ValueError):
            return []


# --- Live posting ----------------------------------------------------------------------
# The client reads thread state and writes comments and statuses through the GitHub proxy.

# Per-call timeouts: 45s for the thread and comment queries, 60s for a comparison, 30s for
# posts and mutations.
READ_TIMEOUT_S = 45.0
COMPARE_TIMEOUT_S = 60.0
WRITE_TIMEOUT_S = 30.0
STATUS_RESERVE_S = 35.0  # what a run keeps back so it can still post the error status it ends on
RUN_OUT_OF_TIME = "run time used up"
_HTTP_STATUS = re.compile(r"\(HTTP (\d{3})\)")
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")

# GitHub thread state decides whether a finding is reposted or answered.
THREAD_STATES_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String = null) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      headRefOid
      reviewThreads(first: 100, after: $after) {
        nodes {
          id
          isResolved
          isOutdated
          comments(first: 1) { nodes { databaseId } }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

# post_review_thread_reply replies with REST ``in_reply_to``, which takes the root comment's numeric
# id and the PR number; a thread's GraphQL node id carries neither, so this read maps one to the other.
REPLY_TARGET_QUERY = """
query($id: ID!) {
  node(id: $id) {
    ... on PullRequestReviewThread {
      comments(first: 1) { nodes { databaseId } }
      pullRequest { number }
    }
  }
}
"""

Runner = Callable[..., str]  # (argv after `corsia github`, json_body_or_None, timeout_s) -> stdout


def _http_status(text: str) -> int | None:
    """The HTTP status in gh's refusal line, e.g. ``gh: … isn't part of the diff (HTTP 422)``."""
    match = _HTTP_STATUS.search(text)
    return int(match.group(1)) if match else None


class GithubWriteFailed(RuntimeError):
    """A GitHub call ran and was refused; ``status`` is the HTTP status gh named, when it named one.
    Reads never raise this: ``LiveClient._run`` reports a failed read as ``GithubUnavailable``,
    the failure every read path in this file already handles."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _cli_write(args: list[str], payload: str | None = None, timeout: float = WRITE_TIMEOUT_S) -> str:
    """One ``corsia github api`` call as the agents' GitHub account, with a JSON body on stdin."""
    target = args[1] if len(args) > 1 else args[0]
    try:
        done = subprocess.run(["corsia", "github", *args], input=payload, capture_output=True,
                              text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GithubWriteFailed(f"GitHub call {target} could not run: {type(exc).__name__}: {exc}") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()[:300]
        raise GithubWriteFailed(f"GitHub call {target} failed: {detail}", _http_status(detail))
    return done.stdout


def _sleep_wall(seconds: float) -> None:
    """The real sleep, looked up when it runs so a test can replace ``time.sleep``."""
    time.sleep(seconds)


class LiveClient:
    """Write review results through the GitHub proxy using the ShadowClient interface."""

    READ_ATTEMPTS = 8       # bounded re-reads of a temporarily unreadable comment history
    READ_INTERVAL_S = 4.0   # seconds between those re-reads

    def __init__(self, runner: Runner | None = None, *, login: str, contexts: Iterable[str] = (),
                 review_contexts: Iterable[str] = (), run_seconds: float | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] | None = None):
        from review_gate_github import Github
        self.runner = runner or _cli_write
        self.github = Github(lambda args: self._run(["api", *args]))
        self.login = login
        self.contexts = {str(context) for context in contexts if context}
        self.review_contexts = self.contexts | {str(context) for context in review_contexts if context}
        self._clock, self._sleep = clock, sleep or _sleep_wall
        # One deadline for the whole run, from the timeout the run was registered with. Without it a
        # run keeps its calls' own timeouts and is killed by the platform instead of publishing a reason.
        self.deadline = None if not run_seconds else clock() + float(run_seconds)

    def _time_left(self, *, reserve: float = STATUS_RESERVE_S) -> float:
        if self.deadline is None: return float("inf")
        return self.deadline - self._clock() - reserve

    def _budget(self, per_call: float, *, final: bool = False) -> float:
        """One call's timeout: its own value, cut to what the run still has. The reserve is spent only
        on the final write it exists for, so the error status fits after everything else has stopped."""
        from review_gate_github import GithubUnavailable
        if self.deadline is None: return per_call
        left = self._time_left(reserve=0.0 if final else STATUS_RESERVE_S)
        if left <= 0: raise GithubUnavailable(RUN_OUT_OF_TIME)
        return min(per_call, left)

    def _run(self, args: list[str], timeout: float = READ_TIMEOUT_S) -> str:
        """One read: a refusal reads as ``GithubUnavailable``, like a failed read through ``Github``."""
        from review_gate_github import GithubUnavailable
        try:
            return self.runner(list(args), None, self._budget(timeout))
        except GithubWriteFailed as exc:
            raise GithubUnavailable(str(exc)) from exc

    def _write(self, args: list[str], payload: Mapping[str, Any], timeout: float = WRITE_TIMEOUT_S, *, final: bool = False) -> str:
        return self.runner(list(args), json.dumps(payload), self._budget(timeout, final=final))

    def _comments(self, path: str) -> list[Mapping[str, Any]]:
        """Every page of one comment collection. A temporarily unreadable comment history is
        re-read up to 8 times, 4s apart, because one GitHub blink must not discard a finished
        review; after that the failure surfaces."""
        from review_gate_github import GithubUnavailable
        last: Exception | None = None
        for attempt in range(self.READ_ATTEMPTS):
            try:
                return self.github.pages(path)
            except GithubUnavailable as exc:  # ValueError: bad JSON
                if str(exc) == RUN_OUT_OF_TIME: raise  # the deadline is not a transient read failure
                last = exc
            except (GithubWriteFailed, ValueError) as exc:
                last = exc
            if attempt + 1 == self.READ_ATTEMPTS: break
            if self._time_left() <= 0: break  # never sleep past a deadline the next call would refuse
            self._sleep(self.READ_INTERVAL_S)
        raise last if last is not None else GithubUnavailable(f"{path} unreadable")

    def current_head(self, repo: str, pr: int) -> str | None:
        """The PR's live head, None when unreadable — as ShadowClient's does, so publish publishes
        on what the run has rather than leaving the head with no verdict."""
        from review_gate_github import GithubUnavailable
        try:
            return str(self.github.json(f"repos/{repo}/pulls/{pr}")["head"]["sha"])
        except (GithubUnavailable, GithubWriteFailed, KeyError, TypeError, ValueError):
            return None

    def _thread_states(self, repo: str, pr: int) -> dict[int, tuple[str, bool, bool]]:
        """GitHub's own state for every review thread at one exact head, keyed by root comment id:
        one paginated GraphQL read, guarded (a repeated root id, a head that moves mid-pagination,
        a stuck cursor, over 100 pages all end the read). There is no marker-only fallback: a
        failed read surfaces here."""
        from review_gate_github import GithubUnavailable

        def unreadable(why: str) -> GithubUnavailable:
            return GithubUnavailable(f"thread lookup for {repo}#{pr} {why}")

        owner, separator, name = repo.partition("/")
        if not separator or not owner or not name or "/" in name:
            raise unreadable(f"got an invalid repo: {repo!r}")
        states: dict[int, tuple[str, bool, bool]] = {}
        after, head = None, None
        for _ in range(100):
            args = ["api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={pr}",
                    "-f", f"query={THREAD_STATES_QUERY}"]
            if after is not None:
                args.extend(["-F", f"after={after}"])
            try:
                payload = json.loads(self._run(args) or "null")
            except ValueError as exc:
                raise unreadable("returned invalid JSON") from exc
            if not isinstance(payload, dict) or payload.get("errors"):
                raise unreadable("returned errors")
            data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
            repository = data.get("repository") if isinstance(data.get("repository"), Mapping) else {}
            pull = repository.get("pullRequest")
            if not isinstance(pull, Mapping):
                raise unreadable("omitted the pull request")
            head_sha = pull.get("headRefOid")
            if not isinstance(head_sha, str) or not _SHA40.fullmatch(head_sha):
                raise unreadable("omitted a valid head SHA")
            if head is not None and head_sha != head:
                raise unreadable("saw the PR head change while paginating")
            head = head_sha
            connection = pull.get("reviewThreads")
            nodes = connection.get("nodes") if isinstance(connection, Mapping) else None
            if not isinstance(nodes, list):
                raise unreadable("returned an incomplete connection")
            for node in nodes:
                if not isinstance(node, Mapping):
                    raise unreadable("returned a malformed thread")
                thread_id, resolved, outdated = node.get("id"), node.get("isResolved"), node.get("isOutdated")
                comments = node.get("comments") if isinstance(node.get("comments"), Mapping) else {}
                roots = comments.get("nodes") if isinstance(comments.get("nodes"), list) else []
                root_id = roots[0].get("databaseId") if roots and isinstance(roots[0], Mapping) else None
                if (not isinstance(thread_id, str) or not thread_id or type(resolved) is not bool
                        or type(outdated) is not bool or type(root_id) is not int or root_id < 1 or root_id in states):
                    raise unreadable("returned incomplete thread identity or state")
                states[root_id] = (thread_id, resolved, outdated)
            page_info = connection.get("pageInfo")
            if not isinstance(page_info, Mapping) or type(page_info.get("hasNextPage")) is not bool:
                raise unreadable("omitted pagination state")
            if not page_info["hasNextPage"]:
                return states
            cursor = page_info.get("endCursor")
            if not isinstance(cursor, str) or not cursor or cursor == after:
                raise unreadable("returned an invalid cursor")
            after = cursor
        raise unreadable("exceeded 100 pages")

    def list_reviewer_findings(self, repo: str, pr: int) -> list[Mapping[str, Any]]:
        """Every reviewer-authored review comment on this PR, oldest first, in the shape the Client
        contract and ShadowClient use: the thread's GraphQL node id (what ``reply`` takes), the body,
        and GitHub's own resolved/outdated for that thread. Bodies and authorship come from the REST
        comment list; only threads whose first comment is the agent's own and
        carries a reviewer marker count, and a thread whose state GitHub did not name is skipped
        rather than guessed at."""
        states = self._thread_states(repo, pr)
        comments = self._comments(f"repos/{repo}/pulls/{pr}/comments")
        roots = {comment.get("id"): comment for comment in comments if not comment.get("in_reply_to_id")}
        # Every id the worker can see for a comment on each thread (its REST id and node id, any
        # author): the staged conversation carries these, never the thread's own node id.
        refs: dict[Any, set[str]] = {}
        for comment in comments:
            root_id = comment.get("in_reply_to_id") or comment.get("id")
            refs.setdefault(root_id, set()).update(str(value) for value in (comment.get("id"), comment.get("node_id")) if value)
        rows: list[tuple[int, dict[str, Any]]] = []
        for comment in comments:
            body, comment_id = comment.get("body"), comment.get("id")
            if not isinstance(body, str) or type(comment_id) is not int or not REVIEWER_MARKER.search(body):
                continue
            root_id = comment_id if not comment.get("in_reply_to_id") else comment.get("in_reply_to_id")
            root, state = roots.get(root_id), states.get(root_id)
            if root is None or state is None:
                continue
            author = root.get("user") if isinstance(root.get("user"), Mapping) else {}
            if str(author.get("login") or "").lower() != self.login.lower():
                continue
            # The cited position belongs to the finding's root comment, so every row on the thread
            # carries it: a reply row must not report the lines the reply happened to sit on.
            end = root.get("original_line") if root.get("original_line") is not None else root.get("line")
            start = root.get("original_start_line") if root.get("original_start_line") is not None else end
            rows.append((comment_id, {"thread_id": state[0], "body": body, "resolved": state[1], "outdated": state[2],
                                      "path": root.get("path"), "start_line": start, "end_line": end,
                                      "source_sha": str(root.get("original_commit_id") or root.get("commit_id") or ""),
                                      "refs": sorted(refs.get(root_id, ()))}))
        return [row for _, row in sorted(rows, key=lambda row: row[0])]

    def upsert_summary(self, repo: str, pr: int, body: str) -> None:
        """Post or edit the sticky top-level summary: append the summary marker to the body and
        edit the comment that already carries it. ``render_summary`` emits no marker of its own,
        so the client appends it; without it every run would post another summary instead of
        editing the one on the PR."""
        marker_body = body if REVIEW_SUMMARY_MARKER in body else f"{body}\n\n{REVIEW_SUMMARY_MARKER}"
        from review_gate_github import GithubUnavailable
        existing = None
        for comment in self._comments(f"repos/{repo}/issues/{pr}/comments"):
            if REVIEW_SUMMARY_MARKER not in str(comment.get("body") or ""):
                continue
            comment_id = comment.get("id")
            if type(comment_id) is not int:  # an id it can't address is a failure
                raise GithubUnavailable(f"{repo}#{pr} summary comment id {comment_id!r} is not addressable")
            existing = comment_id
            break
        if existing is not None:
            self._write(["api", f"repos/{repo}/issues/comments/{existing}", "-X", "PATCH", "--input", "-"], {"body": marker_body})
        else:
            self._write(["api", f"repos/{repo}/issues/{pr}/comments", "-X", "POST", "--input", "-"], {"body": marker_body})

    def post_finding(self, repo: str, pr: int, head: str, finding: Mapping[str, Any], body: str) -> bool:
        """One finding on the head commit at its path and line (``line``+``side: RIGHT`` with a line, ``subject_type:
        file`` without one). False when GitHub refuses the anchor — HTTP 422, i.e. the line is not
        in the diff — or there is no path to anchor on, so publish renders the finding in the
        summary's fallback section; any other refusal raises."""
        path = str(finding.get("path") or "").strip()
        if not path:
            return False
        payload: dict[str, Any] = {"body": body, "commit_id": head, "path": path}
        line = finding.get("line")
        if isinstance(line, int) and not isinstance(line, bool) and line > 0:
            payload.update({"line": line, "side": "RIGHT"})
        else:
            payload["subject_type"] = "file"
        try:
            self._write(["api", f"repos/{repo}/pulls/{pr}/comments", "-X", "POST", "--input", "-"], payload)
        except GithubWriteFailed as exc:
            if exc.status == 422:
                return False
            raise
        return True

    def reply(self, repo: str, thread_id: str, body: str) -> None:
        """Reply in the finding's own thread: a REST post with ``in_reply_to`` the root comment id, which is why one node read maps
        the thread id to that id and the PR number first (the Client contract names no PR here)."""
        from review_gate_github import GithubUnavailable
        try:
            payload = json.loads(self._run(["api", "graphql", "-f", f"query={REPLY_TARGET_QUERY}", "-f", f"id={thread_id}"]) or "null")
        except ValueError as exc:
            raise GithubUnavailable(f"reply target {thread_id} returned invalid JSON") from exc
        data = payload.get("data") if isinstance(payload, Mapping) else None
        node = data.get("node") if isinstance(data, Mapping) else None
        comments = node.get("comments") if isinstance(node, Mapping) else None
        roots = comments.get("nodes") if isinstance(comments, Mapping) else None
        root = roots[0] if isinstance(roots, list) and roots and isinstance(roots[0], Mapping) else None
        pull = node.get("pullRequest") if isinstance(node, Mapping) else None
        root_id = root.get("databaseId") if root else None
        number = pull.get("number") if isinstance(pull, Mapping) else None
        if type(root_id) is not int or root_id < 1 or not isinstance(number, int):
            raise GithubUnavailable(f"reply target {thread_id} is not an addressable review thread")
        self._write(["api", f"repos/{repo}/pulls/{number}/comments", "-X", "POST", "--input", "-"],
                    {"body": body, "in_reply_to": root_id})

    def set_status(self, repo: str, head: str, context: str, state: str, description: str) -> None:
        """Write only the configured verdict and presence contexts."""
        if context not in self.contexts:
            raise ValueError(f"live mode never writes status context {context}")
        self._write(["api", f"repos/{repo}/statuses/{head}", "--method", "POST", "--input", "-"],
                    {"state": state, "description": description[:STATUS_LIMIT], "context": context}, final=True)

    def standing_state(self, repo: str, head: str, context: str) -> str | None:
        """The newest standing state on ``context`` for this head, from the same combined-status read
        ``gh_helpers.get_commit_status_for_context`` makes. None when the context has no status or the
        read fails: that helper fails open, and publish_status's standing-success guard then proceeds."""
        from review_gate_github import GithubUnavailable
        try:
            out = self._run(["api", f"repos/{repo}/commits/{head}/status", "--paginate", "--jq", ".statuses[]"])
        except GithubUnavailable:
            return None
        for line in out.splitlines():
            if not line.strip():
                continue
            try:
                status = json.loads(line)
            except ValueError:
                return None  # the helper returns None on an unparseable entry; callers fail open
            if isinstance(status, Mapping) and status.get("context") == context:
                return str(status.get("state") or "")
        return None

    def cited_line_report(self, repo: str, head: str, prior: Prior) -> str:
        return cited_line_report(prior, head, lambda source, target: self._compare_commits(repo, source, target))

    def _compare_commits(self, repo: str, source_sha: str, head_sha: str) -> tuple[Mapping[str, Any] | None, str]:
        """Read a comparison for the exact SHAs and truncate error details."""
        from review_gate_github import GithubUnavailable
        try:
            data = json.loads(self._run(["api", f"repos/{repo}/compare/{source_sha}...{head_sha}"], COMPARE_TIMEOUT_S) or "null")
        except GithubUnavailable as exc:
            return None, str(exc)[:160]
        except ValueError:
            return None, "GitHub compare returned invalid JSON"
        return _compare_inventory(data)

    def set_thread_resolved(self, repo: str, thread_id: str, resolved: bool) -> bool:
        """GitHub's own review-thread state, set with one GraphQL mutation (``resolveReviewThread``
        or ``unresolveReviewThread``, ``-f query=`` with ``-F threadId=``, no request body) and
        verified: the response must name this thread and the state asked for. False on a
        refusal, GraphQL errors, an unparseable body or an unconfirmed state."""
        mutation = "resolveReviewThread" if resolved else "unresolveReviewThread"
        query = (f"mutation($threadId: ID!) {{ {mutation}(input: {{threadId: $threadId}}) "
                 "{ thread { id isResolved } } }")
        from review_gate_github import GithubUnavailable
        try:
            payload = json.loads(self.runner(["api", "graphql", "-f", f"query={query}", "-F", f"threadId={thread_id}"], None, self._budget(WRITE_TIMEOUT_S)) or "null")
        except (GithubWriteFailed, GithubUnavailable, ValueError):
            return False
        data = payload.get("data") if isinstance(payload, Mapping) else None
        response = data.get(mutation) if isinstance(data, Mapping) else None
        thread = response.get("thread") if isinstance(response, Mapping) else None
        return (isinstance(thread, Mapping) and thread.get("id") == thread_id
                and type(thread.get("isResolved")) is bool and thread["isResolved"] is resolved)

    def failed_checks(self, repo: str, head: str) -> list[str]:
        """The head's failed check names for the waiting status; the same read and filter ShadowClient
        makes (GitHub's own latest attempt per check), [] when unreadable."""
        import review_gate_evaluate as evaluate
        from review_gate_github import GithubUnavailable
        try:
            runs = self.github.json(f"repos/{repo}/commits/{head}/check-runs?per_page=100&filter=latest")["check_runs"]
            return sorted({str(run["name"]) for run in runs if str(run["name"]) not in self.review_contexts
                           and str(run.get("conclusion") or "").lower() in evaluate.FAILED_CONCLUSIONS})
        except (GithubUnavailable, GithubWriteFailed, KeyError, TypeError, ValueError):
            return []


def _moved(client: Client, repo: str, pr: int, head: str) -> bool:
    """Whether the PR's head has moved past ``head``. An unreadable head counts as not moved:
    publishing on what the run has beats leaving the head with no verdict, and a write names
    its own commit, so one made on a superseded head stays on that commit."""
    current = client.current_head(repo, pr)
    return current is not None and current.lower() != head.lower()


#: The statuses that still apply while the head's review is in flight.
IN_FLIGHT_STATES = frozenset({"requested", "skipped"})


def publish_status(facts: Mapping[str, Any], settings: Settings, client: Any, store: Any) -> str:
    """Rule 4b: one ``qa.review_status`` to the verdict context. A verdict is never
    overwritten by a status, nor is a standing success a person posted (G4); only the
    PR's current head counts, and an unchanged status is not written again."""
    import review_gate_evaluate as evaluate
    repo, pr, head, state_name = facts.get("repo"), facts.get("pr"), str(facts.get("head_sha") or "").lower(), facts.get("state")
    if not isinstance(repo, str) or not isinstance(pr, int) or not _is_sha(head) or not isinstance(state_name, str):
        return "invalid_status"
    if _moved(client, repo, pr, head):
        return "not_current_head"
    with store.transaction() as state:
        # Status runs wait in a queue, so one evaluate wrote before this head's review was
        # requested can land after it; while the review is in flight only its own request
        # and a maintainer's skip may show.
        if evaluate.get(state, "in_flight", repo, pr) == head and state_name not in IN_FLIGHT_STATES:
            return "review_in_flight"
        last = evaluate.get(state, "published", repo, pr)
        if isinstance(last, Mapping) and last.get("head") == head:
            if last.get("verdict"):
                return "verdict_already_posted"
            if last.get("status") == state_name:
                return "unchanged"
        if client.standing_state(repo, head, settings.verdict_context) == "success":
            return "success_stands"
        base_ref = facts.get("base_ref")
        failed = client.failed_checks(repo, head) if state_name == "checks_failed" else []
        client.set_status(repo, head, settings.verdict_context,
                          *status_for_state(state_name, settings, base_ref=base_ref if isinstance(base_ref, str) else "", failed=failed))
        evaluate.put(state, "published", repo, pr, {"head": head, "status": state_name, "verdict": False})
    return "status_written"


def run_rule(event: Mapping[str, Any], config: Mapping[str, Any], client: Any, store: Any) -> tuple[str, list[str]]:
    """One rule-started run: a status (4b) or a worker's settled review (4)."""
    import review_gate_config as rule_config
    import review_gate_evaluate as evaluate
    contexts = rule_config.contexts(config)
    settings = Settings(model="", sme=config.get("sme"),
                        verdict_context=contexts["verdict"], presence_context=contexts["presence"])
    facts = event.get("facts")
    if event.get("type") == "qa.review_status":
        return publish_status(facts if isinstance(facts, Mapping) else {}, settings, client, store), []
    if event.get("type") not in {"worker.result", "worker.no_result"}:
        return "not_a_publish_event", []
    envelope = envelope_from_worker(event)
    if envelope is None:
        return "worker_identity_unreadable", []
    settings = Settings(**{**settings.__dict__, "model": envelope["model"] or "unrecorded"})
    identity = evaluate.Identity(envelope["repo"], envelope["pr"], envelope["head_sha"])
    # A settled result that can't be posted is this script's to request again: evaluate's rule 5
    # counts the head reviewed once a result settles, and retries only a worker.no_result.
    owns_retry = event.get("type") == "worker.result"
    with store.transaction() as state:
        if evaluate.get(state, "in_flight", envelope["repo"], envelope["pr"]) not in (None, envelope["head_sha"]):
            return "superseded", []
        failed_before = evaluate._for_head(state, "publish_failures", identity)
    count = (failed_before["n"] if failed_before else 0) + 1
    retrying = not owns_retry or count < int(config["no_result_retry_bound"])
    result = publish(envelope, settings, client, retrying)
    lines = [measure_line(item) for item in result.events]
    if owns_retry and not result.published and result.reason != "superseded":
        out = evaluate.Emitter(config.get("mode") == "shadow")
        with store.transaction() as state:
            evaluate.put(state, "publish_failures", identity.repo, identity.pr, {"head": identity.head_sha, "n": count})
            if retrying:
                evaluate._request(state, identity, "default", out)
            else:
                out.terminal(identity, "not_published_after_retries")
        lines.extend(out.lines)
    if result.published:
        with store.transaction() as state:
            evaluate.put(state, "publish_failures", identity.repo, identity.pr, None)
            evaluate.put(state, "published", envelope["repo"], envelope["pr"],
                         {"head": envelope["head_sha"], "status": None, "verdict": True})
        verdict = verdict_event(envelope, config, settings)
        if config["mode"] == "live":
            lines.append(json.dumps({"event": {"type": "reviewer.verdict", "facts": verdict}}, sort_keys=True))
        else:
            client._write(envelope["repo"], envelope["pr"], "reviewer.verdict", facts=verdict)
    return result.reason, lines


def rule_main() -> int:
    import review_gate_config as rule_config
    import review_gate_evaluate as evaluate
    from review_gate_github import Github, GithubUnavailable
    try:
        config = rule_config.load()
        event = json.load(sys.stdin)
    except (OSError, ValueError) as exc:
        print(f"review-gate publish: invalid input: {exc}", file=sys.stderr)
        return 2
    contexts = rule_config.contexts(config)
    client = (LiveClient(login=config["agents_login"], contexts=contexts.values(), review_contexts=rule_config.all_contexts(config), run_seconds=config.get("run_seconds")) if config["mode"] == "live"
              else ShadowClient(Github(), rule_config.folder(config, "shadow_record"), contexts["verdict"], rule_config.all_contexts(config)))
    try:
        reason, lines = run_rule(event if isinstance(event, Mapping) else {}, config, client,
                                 evaluate.StateStore(rule_config.folder(config, "state")))
    except (GithubUnavailable, KeyError, TypeError) as exc:
        print(f"review-gate publish: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line, flush=True)
    if reason == "invalid_envelope":
        # A result this script cannot read at all is not a published review: fail the run so the owner
        # route reports it, instead of exiting 0 having written nothing.
        print("review-gate publish: refused the worker's result (invalid_envelope): nothing was published", file=sys.stderr)
        return 2
    return 0 if reason not in {"invalid_status", "worker_identity_unreadable"} else 1


def _load(path: Path | None) -> Any:
    if path is None:
        return json.load(sys.stdin)
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:  # a rule-started run
        return rule_main()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", required=True, type=Path, help="JSON: model, show_model_in_output, sme, rules")
    parser.add_argument("--envelope", type=Path, help="JSON review envelope; otherwise read it from stdin")
    parser.add_argument("--dry-run", action="store_true", help="print writes to stderr; required until a GitHub adapter is wired")
    parser.add_argument("--prior", type=Path, help="dry run: JSON list of prior reviewer comments (Client contract)")
    parser.add_argument("--head", help="dry run: the PR's current head; defaults to the envelope's")
    args = parser.parse_args(argv)
    if not args.dry_run:
        print("review-gate publish: no GitHub adapter is wired yet; run with --dry-run", file=sys.stderr)
        return 2
    try:
        raw = _load(args.settings)
        settings = Settings(**{key: raw[key] for key in ("model", "show_model_in_output", "sme", "rules", "standards") if key in raw})
        envelope = _load(args.envelope)
        prior = _load(args.prior) if args.prior else []
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"review-gate publish: invalid input: {exc}", file=sys.stderr)
        return 2
    head = args.head or (envelope.get("head_sha", "") if isinstance(envelope, Mapping) else "")
    result = publish(envelope, settings, DryRunClient(head, prior))
    for event in result.events:
        print(measure_line(event), flush=True)
    return 0 if result.published or result.reason == "superseded" else 1


if __name__ == "__main__":
    import review_gate_config as rule_config

    raise SystemExit(rule_config.timed("publish", main))
