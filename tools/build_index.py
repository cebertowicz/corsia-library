"""Build index.json from local items and reviewed source pins."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library_tools.validation import ValidationError, index_text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=Path("index.json"))
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        text = index_text(root)
    except ValidationError as error:
        parser.error(str(error))
    output = args.output if args.output.is_absolute() else root / args.output
    output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
