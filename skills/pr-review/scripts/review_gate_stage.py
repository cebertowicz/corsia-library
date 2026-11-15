#!/usr/bin/env python3
"""`stage_review` (rule 3a): stage one review's inputs and request the worker.

Started by ``qa.review_stage_requested`` from `evaluate`. Reads the PR, its issue
comments, review comments and reviews, the diff, and the standards files at the PR's
base commit through the run's GitHub read channel (or, when ``standards_local_root``
is set, from that folder under the project, for standards not yet in the repository); writes them under the owner's
staging folder; adds the head commit as a detached git worktree at ``head/`` in the same
folder, from the repository's local checkout (``repo_checkouts``), fetching the commit there
first when it is missing; records which reply ids it staged (rule 5 re-requests a review when a
reply arrived after staging); then prints one ``qa.review_requested`` line for the
start rule (rule 3). It never starts a review on partial input: a failed read counts as
a failed attempt. stage cannot ask for itself again: the platform skips a rule already in the
event's chain. So it
tries again inside the run, up to ``no_result_retry_bound`` tries, STAGE_RETRY_S apart, and
after the last raises one terminal error the PR's owner sees, instead of stalling.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping

import review_gate_config as rule_config
import review_gate_evaluate as evaluate
from review_gate_github import Github, GithubUnavailable

RAW = "application/vnd.github.raw"
DIFF = "application/vnd.github.diff"
GIT_TIMEOUT = 240
STAGE_RETRY_S = 15.0
#: The checkout's own hooks (post-checkout and the rest) never run for a review's worktree.
GIT = ("git", "-c", "core.hooksPath=/dev/null", "-c", "advice.detachedHead=false")


class HeadUnavailable(Exception):
    """The head commit could not be fetched or checked out."""


def _staging_dir(config: Mapping[str, Any], request_identity: str) -> Path:
    return rule_config.folder(config, "staging") / re.sub(r"[^A-Za-z0-9_.-]", "_", request_identity)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def stage(facts: Mapping[str, Any], config: Mapping[str, Any], github: Github,
          store: evaluate.StateStore) -> tuple[str, list[str]]:
    out = evaluate.Emitter()
    request_identity = facts.get("request_identity")
    parsed = rule_config.parse_identity(request_identity)
    if parsed is None:
        out.needs_turn("stage_identity_unreadable")
        return "stage_identity_unreadable", out.lines
    repo, pr, head, attempt = parsed
    identity = evaluate.Identity(repo, pr, head)
    tries = max(1, int(config["no_result_retry_bound"]))
    for tried in range(1, tries + 1):
        try:
            pull = github.json(f"repos/{repo}/pulls/{pr}")
            if str(pull["head"]["sha"]).lower() != head:
                _release(store, identity)
                return "superseded", out.lines  # the newer head's own event reviews it
            base, base_ref = str(pull["base"]["sha"]), str(pull["base"]["ref"])
            comments = github.pages(f"repos/{repo}/issues/{pr}/comments")
            review_comments = github.pages(f"repos/{repo}/pulls/{pr}/comments")
            reviews = github.pages(f"repos/{repo}/pulls/{pr}/reviews")
            diff = github.text(f"repos/{repo}/pulls/{pr}", accept=DIFF)
            standards = _standards(config, github, repo, base)
            folder = _staging_dir(config, request_identity)
            add_head_worktree(_checkout(config, repo), head, folder / "head")
            break
        except (GithubUnavailable, HeadUnavailable, KeyError, TypeError, ValueError) as exc:
            print(f"review-gate stage_review: try {tried} of {tries}: {exc}", file=sys.stderr)
            if tried < tries:
                time.sleep(STAGE_RETRY_S)
                continue
            with store.transaction() as state:
                if evaluate.get(state, "in_flight", repo, pr) != head:
                    return "superseded", out.lines
                evaluate.put(state, "in_flight", repo, pr, None)
                out.terminal(identity, "not_staged_after_retries")
                return "stage_retry_bound", out.lines
    _write(folder / "pr.json", json.dumps(pull, indent=1, sort_keys=True))
    _write(folder / "issue-comments.json", json.dumps(comments, indent=1))
    _write(folder / "review-comments.json", json.dumps(review_comments, indent=1))
    _write(folder / "reviews.json", json.dumps(reviews, indent=1))
    _write(folder / "diff.patch", diff)
    for path, text in standards.items():
        _write(folder / "standards" / Path(path).name, text)
    staged_ids = sorted({str(item["id"]) for item in (*comments, *review_comments, *reviews)
                         if isinstance(item, Mapping) and "id" in item})
    with store.transaction() as state:
        state["staged"][request_identity] = staged_ids
    out.lines.append(evaluate._event_line("qa.review_requested", {
        "repo": repo, "pr": pr, "head_sha": head, "attempt": attempt,
        "request_identity": request_identity,
        "staging_path": folder.name,  # worker_inputs.staging_dir resolves it inside the staging folder
        "base_ref": base_ref,  # rules 3 and 3-main pick the worker profile by it (G2)
        "mode": facts.get("mode", "default")}))
    return "staged", out.lines


def _standards(config: Mapping[str, Any], github: Github, repo: str, base: str) -> dict[str, str]:
    local = config.get("standards_local_root")
    standards = {}
    for path in config["standards"]:
        if local:
            source = rule_config.project_root(config) / local / Path(path).name
            try:
                standards[path] = source.read_text(encoding="utf-8")
            except OSError as exc:
                raise GithubUnavailable(f"standards file {source} unreadable: {exc}") from exc
            continue
        try:
            standards[path] = github.text(f"repos/{repo}/contents/{path}?ref={base}", accept=RAW)
        except GithubUnavailable as exc:
            raise GithubUnavailable(f"standards file {path} missing at base {base[:12]}: {exc}") from exc
    return standards


def _checkout(config: Mapping[str, Any], repo: str) -> Path:
    """The repository's local checkout: a path in ``repo_checkouts``, relative to the project."""
    return rule_config.project_root(config) / config["repo_checkouts"][repo]


def _git(checkout: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run([*GIT, "-C", str(checkout), *args], capture_output=True, text=True,
                              timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HeadUnavailable(f"git {args[0]} in {checkout}: {type(exc).__name__}: {exc}") from exc


def _has_commit(checkout: Path, sha: str) -> bool:
    return _git(checkout, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def add_head_worktree(checkout: Path, sha: str, dest: Path) -> None:
    """Check ``sha`` out at ``dest`` as a detached worktree of ``checkout``.

    Stale entries are pruned first: CORSIA deletes a staged folder when its review
    settles, which leaves the worktree's record in the checkout until the next prune. A
    commit the checkout lacks is fetched from ``origin`` by SHA (fork heads included: GitHub
    serves any commit a pull request's ref reaches).
    """
    _git(checkout, "worktree", "prune")
    if not _has_commit(checkout, sha):
        fetched = _git(checkout, "fetch", "--no-tags", "--no-write-fetch-head", "--quiet", "origin", sha)
        if fetched.returncode or not _has_commit(checkout, sha):
            raise HeadUnavailable(f"git fetch origin {sha[:12]} in {checkout}: {fetched.stderr.strip()[:400]}")
    if dest.exists():  # a stage re-run for the same request: start from a clean tree
        _git(checkout, "worktree", "remove", "--force", str(dest))
        shutil.rmtree(dest, ignore_errors=True)
    added = _git(checkout, "worktree", "add", "--detach", "--quiet", str(dest), sha)
    if added.returncode:
        raise HeadUnavailable(f"git worktree add {sha[:12]} in {checkout}: {added.stderr.strip()[:400]}")


def _release(store: evaluate.StateStore, identity: evaluate.Identity) -> None:
    with store.transaction() as state:
        if evaluate.get(state, "in_flight", identity.repo, identity.pr) == identity.head_sha:
            evaluate.put(state, "in_flight", identity.repo, identity.pr, None)


def main() -> int:
    try:
        config = rule_config.load()
        event = json.load(sys.stdin)
    except (OSError, ValueError) as exc:
        print(f"review-gate stage_review: invalid input: {exc}", file=sys.stderr)
        return 2
    facts = event.get("facts") if isinstance(event, Mapping) else None
    store = evaluate.StateStore(rule_config.folder(config, "state"))
    reason, lines = stage(facts if isinstance(facts, Mapping) else {}, config, Github(), store)
    for line in lines:
        print(line, flush=True)
    return 1 if reason == "stage_identity_unreadable" else 0  # the runner drops stdout on a non-zero exit


if __name__ == "__main__":
    raise SystemExit(rule_config.timed("stage", main))
