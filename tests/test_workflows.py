from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from library_tools.validation import ValidationError, validate_instructions, validate_repository

FIXTURE = Path(__file__).parent / "fixtures" / "good"


@pytest.fixture
def workflow_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    skill = root / "skills" / "example" / "SKILL.md"
    skill.write_text(skill.read_text().replace("  version: 1.0.0", "  version: 1.0.0\n  corsia-data: corsia.yaml\n  corsia-format: '1'"))
    (skill.parent / "corsia.yaml").write_text("workflow:\n  steps:\n    - id: first\n      required: true\n      check: Done\n")
    return root


def test_valid_workflow(workflow_repo: Path) -> None:
    validate_repository(workflow_repo)


@pytest.mark.parametrize(("sidecar", "rule", "detail"), [
    ("handler: example\nworkflow: {}\n", "RULE_SIDECAR_KEYS", "handler"),
    ("origin: example\nworkflow: {}\n", "RULE_SIDECAR_KEYS", "origin"),
    ("- not a mapping\n", "RULE_SIDECAR_KEYS", "mapping"),
    ("workflow:\n  unknown: true\n", "RULE_WORKFLOW_KEYS", "unknown"),
    ("workflow:\n  steps:\n    - id: first\n      required: true\n", "RULE_WORKFLOW", "check is required"),
    ("workflow:\n  steps:\n    - id: first\n      workflow: [other]\n", "RULE_WORKFLOW", "non-empty string"),
    ("workflow:\n  steps:\n    - id: first\n      workflow: absent\n", "RULE_WORKFLOW_NESTED", "absent"),
])
def test_seeded_workflow_refusals(workflow_repo: Path, sidecar: str, rule: str, detail: str) -> None:
    (workflow_repo / "skills/example/corsia.yaml").write_text(sidecar)
    with pytest.raises(ValidationError, match=rule) as error:
        validate_repository(workflow_repo)
    assert detail in str(error.value)


@pytest.mark.parametrize("change", ["missing_pointer", "wrong_format", "missing_file"])
def test_sidecar_pointer_refusals(workflow_repo: Path, change: str) -> None:
    skill = workflow_repo / "skills/example/SKILL.md"
    if change == "missing_file":
        (skill.parent / "corsia.yaml").unlink()
    elif change == "missing_pointer":
        skill.write_text(skill.read_text().replace("  corsia-data: corsia.yaml\n", ""))
    else:
        skill.write_text(skill.read_text().replace("corsia-format: '1'", "corsia-format: '2'"))
    with pytest.raises(ValidationError, match="RULE_SIDECAR_POINTER"):
        validate_repository(workflow_repo)


def test_case_insensitive_sidecar_name(workflow_repo: Path) -> None:
    path = workflow_repo / "skills/example/corsia.yaml"
    path.rename(path.with_name("CORSIA.YAML"))
    validate_repository(workflow_repo)


def test_nested_workflow_resolves(workflow_repo: Path) -> None:
    skill = workflow_repo / "skills/example"
    other = skill.parent / "other"
    shutil.copytree(skill, other)
    metadata = other / "SKILL.md"
    metadata.write_text(metadata.read_text().replace("name: example", "name: other"))
    (skill / "corsia.yaml").write_text("workflow:\n  steps:\n    - id: nested\n      workflow: other\n")
    validate_repository(workflow_repo)
    (other / "corsia.yaml").unlink()
    metadata.write_text(metadata.read_text().replace("  corsia-data: corsia.yaml\n", ""))
    with pytest.raises(ValidationError, match="RULE_WORKFLOW_NESTED"):
        validate_repository(workflow_repo)


def test_sidecar_still_gets_public_content_checks(workflow_repo: Path) -> None:
    (workflow_repo / "skills/example/corsia.yaml").write_text("workflow: {}\n# AGENT-SEND\n")
    with pytest.raises(ValidationError, match="RULE_PUBLIC_CONTENT"):
        validate_repository(workflow_repo)


@pytest.mark.parametrize(("instruction", "rule"), [
    ("'# Example'", None),
    ("'# Missing'", "RULE_WORKFLOW_INSTRUCTION"),
    ("Drafting", "RULE_WORKFLOW"),
])
def test_instruction_end_to_end(workflow_repo: Path, instruction: str, rule: str | None) -> None:
    (workflow_repo / "skills/example/corsia.yaml").write_text(
        f"workflow:\n  steps:\n    - id: first\n      check: Done\n      instruction: {instruction}\n"
    )
    if rule is None:
        validate_repository(workflow_repo)
    else:
        with pytest.raises(ValidationError, match=rule):
            validate_repository(workflow_repo)


@pytest.mark.parametrize(("step", "detail"), [
    ("by: agent", None),
    ("by: reviewer", None),
    ("by: person", None),
    ("by: user", "by must be one of agent, reviewer, person"),
    ("by: person\n      workflow: other", "can't be set on a step that runs a workflow"),
])
def test_by_end_to_end(workflow_repo: Path, step: str, detail: str | None) -> None:
    shutil.copytree(workflow_repo / "skills/example", workflow_repo / "skills/other")
    other = workflow_repo / "skills/other/SKILL.md"
    other.write_text(other.read_text().replace("name: example", "name: other"))
    (workflow_repo / "skills/example/corsia.yaml").write_text(
        f"workflow:\n  steps:\n    - id: first\n      check: Done\n      {step}\n"
    )
    if detail is None:
        validate_repository(workflow_repo)
    else:
        with pytest.raises(ValidationError, match="RULE_WORKFLOW") as error:
            validate_repository(workflow_repo)
        assert detail in str(error.value)


@pytest.mark.parametrize(("instruction", "valid"), [("# Example", True), ("# Missing", False), ("Use this fixture only to verify the library envelope.", False), (None, False)])
def test_instruction_heading(workflow_repo: Path, instruction: object, valid: bool) -> None:
    skill = workflow_repo / "skills/example"
    workflow = {"steps": [{"id": "first", "instruction": instruction}]}
    if valid:
        validate_instructions(skill, workflow)
    else:
        with pytest.raises(ValidationError, match="RULE_WORKFLOW_INSTRUCTION"):
            validate_instructions(skill, workflow)
