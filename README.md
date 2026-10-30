# CORSIA Library

Shared Agent Skills, workflows, and blueprints for CORSIA agents.

Contributions are not open yet.

## Names

Every skill, pack and outside pin has one name, and CI refuses a name that breaks these rules:

- It uses lowercase letters, digits and hyphens, and starts with a letter.
- No two items share a name, whether they are skills, packs or pins.
- It is not on `reserved-names.yaml`, the names the CORSIA platform keeps for itself.
- It does not contain `default`, `official` or `recommended`.

A name never changes once taken. A withdrawn item keeps its name and stays in `index.json`, so nothing can take it over.

## Workflows

A workflow is a skill with a `corsia.yaml` containing `workflow:`. CI refuses a missing or incorrect SKILL.md pointer, other sidecar keys, workflow keys besides `steps` and `result`, invalid workflow data, and nested workflows that do not name another workflow skill in this library. Step instructions must name a heading in the skill's SKILL.md.

Licensed under Apache-2.0.
