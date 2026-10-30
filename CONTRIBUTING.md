# Contributing to the CORSIA Library

Contributions are open to people the maintainers have invited. Open contributions come later, once the automated safety checks are in place.

## What you can contribute

- **A fix or improvement to a skill or workflow already in `skills/`.**
- **A new skill or workflow.** A workflow is a skill whose `corsia.yaml` holds a `workflow:` mapping. The README's "Workflows" section has the details.

Outside skills listed in `sources.yaml` are pinned copies. Change those in their own repositories, not here.

## How

1. Fork this repository and make a branch.
2. Change one item per pull request: one folder under `skills/`.
3. Bump `metadata.version` in that item's `SKILL.md`: patch for a fix, minor for something new, major when the change breaks existing use.
4. Run the checks locally, the same ones CI runs:
   ```
   python3 -m venv .venv
   .venv/bin/pip install -r requirements-dev.txt
   .venv/bin/python tools/check_library.py --root .
   .venv/bin/python -m pytest -q
   ```
5. Open the pull request and fill in the template. Leave `index.json` and `sources.yaml` alone: a maintainer lists your change in a follow-up after it merges.

## What CI refuses

The README's Names, Files and Workflows sections list every rule. In short, CI refuses:
- any file that could run as code: text files other than Markdown, plain text, YAML, JSON and CSV, and executables, symlinks and dotfiles, wherever they sit (images and fonts are allowed in `assets/`);
- email addresses, home-directory paths and anything that looks like a credential;
- a `corsia.yaml` with anything other than `workflow:`, or a workflow that doesn't pass CORSIA's own checks.

## Review

A maintainer reviews each pull request before it merges. Expect questions, not silence. The library is public and licensed Apache-2.0, so everything you submit is published under that license, permanently.
