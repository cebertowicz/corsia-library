"""Check vendored workflow validation against the pinned upstream files."""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.request import urlopen

import yaml

VENDOR = Path(__file__).resolve().parents[1] / "library_tools" / "vendor"


def main() -> int:
    source = yaml.safe_load((VENDOR / "SOURCE.yaml").read_text(encoding="utf-8"))
    for name, expected in source["files"].items():
        url = f"https://raw.githubusercontent.com/cebertowicz/corsia/{source['commit']}/backend/core/{name}"
        with urlopen(url, timeout=30) as response:
            upstream = response.read()
        if hashlib.sha256(upstream).hexdigest() != expected:
            raise ValueError(f"{name}: upstream hash differs from SOURCE.yaml")
        local = (VENDOR / name).read_bytes()
        if name == "skill_workflow_validation.py":
            original = b"from core.workflow_result import schema_errors"
            replacement = b"from .workflow_result import schema_errors"
            if upstream.count(original) != 1:
                raise ValueError(f"{name}: expected import not found exactly once")
            upstream = upstream.replace(original, replacement)
        if local != upstream:
            raise ValueError(f"{name}: vendored file differs from upstream")
        print(f"{name}: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
