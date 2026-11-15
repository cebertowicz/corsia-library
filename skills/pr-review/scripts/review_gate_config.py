"""The review rule set's one configuration file, read by every rule-run script.

A rule-started script gets no arguments (``automation.script_sandbox.script_command``),
so each script reads ``rule-set.json`` beside the pack and resolves its state, staging
and record folders under the owner's thread root, which holds this pack at
``<thread root>/skills/pr-review/``. ``staging`` must be the owner's staging folder,
the only one ``worker_inputs.staging_dir`` lets a worker read.
"""
from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping

PACK = Path(__file__).resolve().parents[1]
CONFIG = PACK / "rule-set.json"


def load(path: Path | None = None) -> dict[str, Any]:
    with (path or CONFIG).open(encoding="utf-8") as handle:
        config = json.load(handle)
    if config.get("mode") not in {"shadow", "live"}:
        raise ValueError("rule-set.json mode must be shadow or live")
    return config


def thread_root(config: Mapping[str, Any]) -> Path:
    override = config.get("thread_root")
    return Path(override).resolve() if override else PACK.parents[1]


def project_root(config: Mapping[str, Any]) -> Path:
    """The project folder holding ``.threads/<owner id>/``."""
    return thread_root(config).parents[1]


def folder(config: Mapping[str, Any], name: str) -> Path:
    return thread_root(config) / config["paths"][name]


def contexts(config: Mapping[str, Any]) -> Mapping[str, str | None]:
    return config["status_contexts"][config["mode"]]


def all_contexts(config: Mapping[str, Any]) -> set[str]:
    return {value for mode in config["status_contexts"].values() for value in mode.values() if value}


def configured_rules(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Resolve optional worker profile rules before registering event rules."""
    import copy
    rules = copy.deepcopy(config["rules"])
    base = config.get("extended_window_base")
    worker = config["worker_workflow"]
    for rule in rules:
        if rule.get("action") == "start_worker":
            rule["workflow"] = worker
        for match in rule.get("match", []):
            if match.get("field") == "workflow" and match.get("value") in {"pr-reviewer", "pr-reviewer@"}:
                match["value"] = worker + ("@" if match["value"].endswith("@") else "")
    if not base:
        rules = [rule for rule in rules if rule.get("id") != "3-start-main"]
        for rule in rules:
            if rule.get("id") == "3-start":
                rule["match"] = [m for m in rule["match"] if m.get("field") != "base_ref"]
    else:
        for rule in rules:
            for match in rule.get("match", []):
                if match.get("value") == "$extended_window_base":
                    match["value"] = base
    return rules


def request_identity(repo: str, pr: int, head_sha: str, attempt: int) -> str:
    """R24's request identity: PR, head and attempt. The start rule fixes the workflow."""
    return f"{repo}#{pr}@{head_sha}:{attempt}"


def timed(script: str, run: Callable[[], int]) -> int:
    """Run one script and append its wall time to ``run-times.jsonl`` in the state folder, the
    only record of how long a rule-started run takes (the platform keeps no start time). A
    failed append never changes the run's result."""
    started = time.time()
    code = 1
    try:
        code = run()
        return code
    finally:
        with contextlib.suppress(Exception):
            line = {"script": script, "started": round(started, 3), "seconds": round(time.time() - started, 3), "exit": code}
            with (folder(load(), "state") / "run-times.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps(line) + "\n")


def parse_identity(value: Any) -> tuple[str, int, str, int] | None:
    if not isinstance(value, str) or "#" not in value or "@" not in value or ":" not in value:
        return None
    repo, _, rest = value.partition("#")
    pr, _, rest = rest.partition("@")
    head, _, attempt = rest.rpartition(":")
    if not (pr.isdigit() and attempt.isdigit() and head):
        return None
    return repo, int(pr), head, int(attempt)
