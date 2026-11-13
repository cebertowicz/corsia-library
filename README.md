# CORSIA Library

Shared Agent Skills, workflows, and blueprints for CORSIA agents.

Contributions are open to invited contributors. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Names

Every skill, pack and outside pin has one name, and CI refuses a name that breaks these rules:

- It uses lowercase letters, digits and hyphens, and starts with a letter.
- No two items share a name, whether they are skills, packs or pins.
- It is not on `reserved-names.yaml`, the names the CORSIA platform keeps for itself.
- It does not contain `default`, `official` or `recommended`.

A name never changes once taken. A withdrawn item keeps its name and stays in `index.json`, so nothing can take it over.

## Files

An item may carry scripts, but only in its top-level `scripts/` folder, as in the Agent Skills format:

- Scripts go in `scripts/` at the item's top level and must be UTF-8 text. They're committed without the executable bit, so call them through their interpreter (for example `python3 scripts/run.py`).
- Other text files must be Markdown, plain text, YAML, JSON or CSV, or be named `LICENSE`, `NOTICE` or `COPYING`.
- Images and fonts are allowed only in `assets/`.
- Code outside `scripts/`, binaries other than those assets, executable files, symlinks, hard links and dotfiles are refused, wherever they sit.
- Email addresses, home-directory paths and anything that looks like a credential are refused.

## Workflows

A workflow is a skill with a `corsia.yaml` containing `workflow:`. CI refuses a missing or incorrect SKILL.md pointer, other sidecar keys, workflow keys besides `steps` and `result`, invalid workflow data, and nested workflows that do not name another workflow skill in this library. Step instructions must name a heading in the skill's SKILL.md.

## Publishing

An item under `skills/` or `packs/` is checked on every change, and merging it doesn't list it. It has no index row until a pin names a commit on the default branch, so people always install bytes that were reviewed and merged.

To list or update an item, add or change its entry under `own_pins` in `sources.yaml`: `name`, `repo: cebertowicz/corsia-library`, `path` (`skills/<name>` or `packs/<name>`), and the full merged `commit` SHA, quoted. Then run `python tools/build_index.py --root .` and commit `index.json`. The pinned commit must be an ancestor of HEAD. Until the pin changes, the index keeps serving the earlier commit. Pins go in their own pull request, after the item's has merged. A squash merge is fine.

A blueprint's `manifest.yaml` also lists each non-manifest file and its SHA-256, and its folder includes `CHANGELOG.md`.

## Browser step files

A skill may include `steps/*.json` in the browser runner's format. CI checks every `.json` under a `steps` folder: each declared domain is a registrable domain on a known public suffix, so no local names or IP addresses; the step shape; addresses outside those domains in typed values and in URLs, redirect targets included; scripted clicks; and a word list for sensitive actions. The word list cannot determine intent: synonyms, obfuscated terms, and harmful actions behind innocuous labels may pass. Reviewers must inspect every step, including whether the declared domains belong to the intended app. Nothing automatic can tell what a page shows, so reviewers must also refuse a step file that goes to a page showing a token or key in the clear.

Licensed under Apache-2.0.
