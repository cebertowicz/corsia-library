"""Git in a reviewed repository's local checkout (``repo_checkouts`` in rule-set.json), shared by
stage and publish. The checkout's own hooks never run. A commit the checkout lacks is fetched from
``origin`` by SHA (fork heads included: GitHub serves any commit a pull request's ref reaches).
"""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any, Mapping

import review_gate_config as rule_config

GIT_TIMEOUT = 240
GIT = ("git", "-c", "core.hooksPath=/dev/null", "-c", "advice.detachedHead=false")


class HeadUnavailable(Exception):
    """A commit could not be fetched, or a git step in the checkout failed."""


def checkout(config: Mapping[str, Any], repo: str) -> Path:
    """The repository's local checkout: a path in ``repo_checkouts``, relative to the project."""
    return rule_config.project_root(config) / config["repo_checkouts"][repo]


def git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run([*GIT, "-C", str(path), *args], capture_output=True, text=True,
                              timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HeadUnavailable(f"git {args[0]} in {path}: {type(exc).__name__}: {exc}") from exc


def has_commit(path: Path, sha: str) -> bool:
    return git(path, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def ensure_commit(path: Path, sha: str) -> None:
    if not has_commit(path, sha):
        fetched = git(path, "fetch", "--no-tags", "--no-write-fetch-head", "--quiet", "origin", sha)
        if fetched.returncode or not has_commit(path, sha):
            raise HeadUnavailable(f"git fetch origin {sha[:12]} in {path}: {fetched.stderr.strip()[:400]}")


def diff(path: Path, *revisions: str) -> str:
    """``git diff`` of ``revisions`` with no colour or external diff tool."""
    done = git(path, "diff", "--no-color", "--no-ext-diff", *revisions)
    if done.returncode:
        raise HeadUnavailable(f"git diff {' '.join(revisions)[:120]} in {path}: {done.stderr.strip()[:400]}")
    return done.stdout
