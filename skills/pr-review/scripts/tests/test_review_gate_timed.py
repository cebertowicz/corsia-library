import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import review_gate_config as rule_config  # noqa: E402


def _config(root: Path) -> dict:
    return {"mode": "live", "thread_root": str(root), "paths": {"state": "state/review-gate"}}


def test_timed_appends_one_line_and_returns_exit(tmp_path, monkeypatch):
    (tmp_path / "state/review-gate").mkdir(parents=True)
    monkeypatch.setattr(rule_config, "load", lambda path=None: _config(tmp_path))
    assert rule_config.timed("evaluate", lambda: 3) == 3
    lines = (tmp_path / "state/review-gate/run-times.jsonl").read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["script"] == "evaluate" and entry["exit"] == 3 and entry["seconds"] >= 0


def test_timed_failed_append_keeps_exit(tmp_path, monkeypatch):
    # No state folder: the append fails and the run's exit code is unchanged.
    monkeypatch.setattr(rule_config, "load", lambda path=None: _config(tmp_path))
    assert rule_config.timed("publish", lambda: 0) == 0
    assert not (tmp_path / "state").exists()
