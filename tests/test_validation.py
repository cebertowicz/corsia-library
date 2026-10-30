from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from library_tools.validation import ValidationError, build_index, check_names, check_names_kept, validate_repository, validate_version_change

FIXTURES = Path(__file__).parent / "fixtures"
REPOSITORY = Path(__file__).parents[1]


@pytest.mark.parametrize(
    ("fixture", "rule"),
    [
        ("short_sha", "RULE_PIN_SHA"),
        ("disallowed_license", "RULE_LICENSE"),
        ("scripts_directory", "RULE_NO_SCRIPTS"),
        ("script_elsewhere", "RULE_NO_SCRIPTS"),
        ("symlink", "RULE_NO_SYMLINK"),
        ("email_address", "RULE_PUBLIC_CONTENT"),
        ("home_path", "RULE_PUBLIC_CONTENT"),
        ("flow_yaml", "RULE_NO_FLOW_YAML"),
        ("pin_without_version", "RULE_VERSION"),
        ("name_format", "RULE_NAME_FORMAT"),
        ("name_held", "RULE_NAME_HELD"),
        ("name_reserved", "RULE_NAME_RESERVED"),
        ("name_claim", "RULE_NAME_CLAIM"),
    ],
)
def test_seeded_refusals(fixture: str, rule: str) -> None:
    with pytest.raises(ValidationError, match=rule):
        validate_repository(FIXTURES / fixture)


def test_good_fixture_passes() -> None:
    index = build_index(FIXTURES / "good")
    assert index["$schema"].endswith("/0.2.0/schema.json")
    assert index["skills"][0]["digest"].startswith("sha256:")


def test_unchanged_version_after_edit_is_refused() -> None:
    fixture = FIXTURES / "unchanged_version_after_edit"
    with pytest.raises(ValidationError, match="RULE_VERSION_BUMP"):
        validate_version_change(fixture / "candidate", fixture / "baseline", Path("skills/example"))


def test_pin_name_held_by_a_skill_is_refused(tmp_path: Path) -> None:
    shutil.copytree(FIXTURES / "good", tmp_path / "repo")
    (tmp_path / "repo" / "sources.yaml").write_text("sources:\n  - name: example\n")
    with pytest.raises(ValidationError, match="RULE_NAME_HELD") as error:
        check_names(tmp_path / "repo")
    assert "pins/example" in str(error.value) and "skills/example" in str(error.value)


@pytest.mark.parametrize("name", ["1st", "-dash", "Upper", "under_score", "", None])
def test_pin_name_format_is_refused(tmp_path: Path, name: object) -> None:
    shutil.copytree(FIXTURES / "good", tmp_path / "repo")
    (tmp_path / "repo" / "sources.yaml").write_text(json.dumps({"sources": [{"name": name}]}))
    with pytest.raises(ValidationError, match="RULE_NAME_FORMAT"):
        check_names(tmp_path / "repo")


@pytest.mark.parametrize("name", ["my-default-skill", "officialpdf", "recommended-setup"])
def test_claiming_words_are_refused_anywhere_in_a_name(tmp_path: Path, name: str) -> None:
    shutil.copytree(FIXTURES / "good", tmp_path / "repo")
    (tmp_path / "repo" / "sources.yaml").write_text(json.dumps({"sources": [{"name": name}]}))
    with pytest.raises(ValidationError, match="RULE_NAME_CLAIM"):
        check_names(tmp_path / "repo")


def test_missing_reserved_file_is_refused(tmp_path: Path) -> None:
    shutil.copytree(FIXTURES / "good", tmp_path / "repo")
    (tmp_path / "repo" / "reserved-names.yaml").unlink()
    with pytest.raises(ValidationError, match="RULE_RESERVED_FILE"):
        check_names(tmp_path / "repo")


def test_published_library_names_pass() -> None:
    check_names(REPOSITORY)
    names = {entry["name"] for entry in json.loads((REPOSITORY / "index.json").read_text())["skills"]}
    assert {"huggingface-papers", "internal-comms", "pdf"} <= names


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args], cwd=root, check=True, capture_output=True)


def test_name_in_base_index_cannot_leave_the_index(tmp_path: Path) -> None:
    index = lambda *names: json.dumps({"skills": [{"name": name} for name in names]})
    git(tmp_path, "init", "-q")
    (tmp_path / "index.json").write_text(index("kept", "gone"))
    git(tmp_path, "add", "index.json")
    git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "index.json").write_text(index("kept", "gone"))
    check_names_kept(tmp_path, "HEAD")
    (tmp_path / "index.json").write_text(index("kept", "new"))
    with pytest.raises(ValidationError, match="RULE_NAME_HELD") as error:
        check_names_kept(tmp_path, "HEAD")
    assert "'gone'" in str(error.value)
