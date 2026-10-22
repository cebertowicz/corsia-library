"""Run the library-specific CI checks."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library_tools.validation import ValidationError, check_names_kept, check_version_bumps, index_text, validate_repository


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--base")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        validate_repository(root)
        if (root / "index.json").read_text(encoding="utf-8") != index_text(root):
            raise ValidationError("RULE_INDEX", "index", "index.json", "committed index does not equal a fresh build")
        if args.base:
            check_names_kept(root, args.base)
            check_version_bumps(root, args.base)
    except ValidationError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
