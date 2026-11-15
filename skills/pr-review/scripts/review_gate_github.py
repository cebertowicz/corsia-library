"""Read-only GitHub access for rule-run scripts, through ``corsia github api``.

CORSIA puts its ``corsia`` client on an approved authenticated script run's PATH
(``automation/script_sandbox.py``). ``corsia github api <args>`` runs ``gh api`` as the
agents' GitHub account and answers with its stdout, stderr and exit code. These scripts
only read: every call is a GET.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Callable

PAGE = 100
READ_TIMEOUT_S = 60


class GithubUnavailable(RuntimeError):
    """This run has no read channel, or a read failed; the caller reports it, never guesses."""


def _cli_read(args: list[str]) -> str:
    try:
        done = subprocess.run(["corsia", "github", "api", *args], capture_output=True, text=True,
                              timeout=READ_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GithubUnavailable(f"GitHub read {args[0]} could not run: {type(exc).__name__}: {exc}") from exc
    if done.returncode != 0:
        raise GithubUnavailable(f"GitHub read {args[0]} failed: {done.stderr.strip()[:300]}")
    return done.stdout


Reader = Callable[[list[str]], str]


class Github:
    def __init__(self, reader: Reader = _cli_read):
        self.reader = reader

    def text(self, path: str, accept: str | None = None) -> str:
        return self.reader([path, *(["-H", f"Accept: {accept}"] if accept else [])])

    def json(self, path: str) -> Any:
        return json.loads(self.text(path) or "null")

    def pages(self, path: str) -> list[Any]:
        """Every item of a list endpoint, ``per_page=100`` page by page."""
        items: list[Any] = []
        page = 1
        while True:
            joiner = "&" if "?" in path else "?"
            batch = self.json(f"{path}{joiner}per_page={PAGE}&page={page}")
            if not isinstance(batch, list):
                raise GithubUnavailable(f"{path} did not return a list")
            items.extend(batch)
            if len(batch) < PAGE:
                return items
            page += 1
