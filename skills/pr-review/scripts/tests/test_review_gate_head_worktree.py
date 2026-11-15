"""stage's head step against real git: a detached worktree from the local checkout."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest
fixture = pytest.fixture
parametrize = pytest.mark.parametrize

sys.path.insert(0, str(Path(__file__).parents[1]))
import review_gate_stage as stage  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@localhost", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, name: str, body: str) -> str:
    (repo / name).write_text(body)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


@fixture
def upstream(tmp_path):
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    commit(repo, "a.txt", "one")
    return repo


@fixture
def checkout(tmp_path, upstream):
    git(tmp_path, "clone", "-q", str(upstream), "checkout")
    return tmp_path / "checkout"


def worktrees(checkout: Path) -> list[str]:
    return [line for line in git(checkout, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]


def test_a_commit_the_checkout_has_is_added_detached_with_origin_unreachable(tmp_path, checkout, upstream):
    sha = git(checkout, "rev-parse", "HEAD")
    git(checkout, "remote", "set-url", "origin", str(tmp_path / "gone"))  # any fetch would now fail
    dest = tmp_path / "staging" / "req" / "head"
    stage.add_head_worktree(checkout, sha, dest)
    assert (dest / "a.txt").read_text() == "one"
    assert git(dest, "rev-parse", "HEAD") == sha
    assert subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=dest).returncode == 1  # detached


def test_a_missing_commit_is_fetched_from_origin_first(tmp_path, checkout, upstream):
    git(upstream, "checkout", "-q", "-b", "pr")
    sha = commit(upstream, "b.txt", "two")
    git(upstream, "checkout", "-q", "main")  # the head is on a ref the clone never fetched
    git(upstream, "config", "uploadpack.allowAnySHA1InWant", "true")  # GitHub serves reachable SHAs
    dest = tmp_path / "staging" / "req" / "head"
    stage.add_head_worktree(checkout, sha, dest)
    assert (dest / "b.txt").read_text() == "two"


def test_an_unknown_commit_raises_and_adds_nothing(tmp_path, checkout):
    dest = tmp_path / "staging" / "req" / "head"
    with pytest.raises(stage.HeadUnavailable, match="git fetch origin"):
        stage.add_head_worktree(checkout, "c" * 40, dest)
    assert not dest.exists() and len(worktrees(checkout)) == 1


def test_a_deleted_staging_folder_is_pruned_and_the_same_place_reused(tmp_path, checkout):
    import shutil
    sha = git(checkout, "rev-parse", "HEAD")
    dest = tmp_path / "staging" / "req" / "head"
    stage.add_head_worktree(checkout, sha, dest)
    shutil.rmtree(tmp_path / "staging")  # what the backend does when the review settles
    stage.add_head_worktree(checkout, sha, tmp_path / "staging" / "other" / "head")
    listed = worktrees(checkout)
    assert len(listed) == 2 and not any(str(dest) in line for line in listed)
    stage.add_head_worktree(checkout, sha, tmp_path / "staging" / "other" / "head")  # a re-run of one request
    assert len(worktrees(checkout)) == 2


@parametrize("configured", [False, True])
def test_no_hook_of_the_checkout_runs(tmp_path, checkout, configured):
    hooks = tmp_path / "hooks" if configured else checkout / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    marker = tmp_path / "hook-ran"
    (hooks / "post-checkout").write_text(f"#!/bin/sh\ntouch {marker}\n")
    (hooks / "post-checkout").chmod(0o755)
    if configured:
        git(checkout, "config", "core.hooksPath", str(hooks))
    stage.add_head_worktree(checkout, git(checkout, "rev-parse", "HEAD"), tmp_path / "staging" / "req" / "head")
    assert (tmp_path / "staging" / "req" / "head" / "a.txt").exists()
    assert not marker.exists()


def test_a_worktree_that_cannot_be_added_raises(tmp_path, checkout):
    (tmp_path / "staging").write_text("a file where the staging folder should be")
    with pytest.raises(stage.HeadUnavailable, match="git worktree add"):
        stage.add_head_worktree(checkout, git(checkout, "rev-parse", "HEAD"), tmp_path / "staging" / "req" / "head")
