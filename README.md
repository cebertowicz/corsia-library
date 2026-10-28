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

An item holds instructions only, and CI refuses any file that could run as code:

- Text files must be Markdown, plain text, YAML, JSON or CSV, or be named `LICENSE`, `NOTICE` or `COPYING`. Code goes in fenced blocks inside Markdown.
- Images and fonts are allowed only in `assets/`.
- Scripts, executables, symlinks, hard links and dotfiles are refused, wherever they sit.
- Email addresses, home-directory paths and anything that looks like a credential are refused.

## Workflows

A workflow is a skill with a `corsia.yaml` containing `workflow:`. CI refuses a missing or incorrect SKILL.md pointer, other sidecar keys, workflow keys besides `steps` and `result`, invalid workflow data, and nested workflows that do not name another workflow skill in this library. Step instructions must name a heading in the skill's SKILL.md.

## Publishing

An item under `skills/` or `packs/` is checked on every change, and merging it doesn't list it. It has no index row until a pin names a commit on the default branch, so people always install bytes that were reviewed and merged.

To list or update an item, add or change its entry under `own_pins` in `sources.yaml`: `name`, `repo: cebertowicz/corsia-library`, `path` (`skills/<name>` or `packs/<name>`), and the full merged `commit` SHA, quoted. Then run `python tools/build_index.py --root .` and commit `index.json`. The pinned commit must be an ancestor of HEAD. Until the pin changes, the index keeps serving the earlier commit. Pins go in their own pull request, after the item's has merged. A squash merge is fine.

A blueprint's `manifest.yaml` also lists each non-manifest file and its SHA-256, and its folder includes `CHANGELOG.md`.

## Browser step files

A skill may include `steps/*.json` in the browser runner's format. CI checks declared registrable domains, the step shape, addresses outside those domains in typed values, scripted clicks, and a word list for sensitive actions. The word list cannot determine intent: synonyms, obfuscated terms, and harmful actions behind innocuous labels may pass. Reviewers must inspect every step, including whether the declared domains belong to the intended app.

Licensed under Apache-2.0.
