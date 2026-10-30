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

Licensed under Apache-2.0.
