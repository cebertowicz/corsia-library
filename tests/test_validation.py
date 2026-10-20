from __future__ import annotations

from pathlib import Path

import pytest

from library_tools.validation import ValidationError, build_index, validate_repository, validate_version_change

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("fixture", "rule"),
    [
        ("short_sha", "RULE_PIN_SHA"),
        ("disallowed_license", "RULE_LICENSE"),
        ("scripts_directory", "RULE_NO_SCRIPTS"),
        ("symlink", "RULE_NO_SYMLINK"),
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
