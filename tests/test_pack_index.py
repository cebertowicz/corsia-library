from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from library_tools.validation import ValidationError, build_index, index_text

FIXTURE = Path(__file__).parent / "fixtures" / "blueprint" / "example"
CHECK = Path(__file__).parents[1] / "tools" / "check_library.py"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "sources.yaml").write_text("sources: []\n")
    (root / "reserved-names.yaml").write_text("reserved: []\n")
    shutil.copytree(FIXTURE, root / "packs" / "example")
    return root


def check(root: Path) -> None:
    subprocess.run([sys.executable, str(CHECK), "--root", str(root)], check=True, capture_output=True, text=True)


def test_pending_pack_is_safe_but_not_indexed(tmp_path: Path) -> None:
    root = repo(tmp_path)
    assert build_index(root)["skills"] == []


@pytest.mark.parametrize(("case", "rule"), [
    ("symlink", "RULE_NO_SYMLINK"),
    ("dotfile", "RULE_NO_DOTFILE"),
    ("scripts", "RULE_NO_SCRIPTS"),
    ("executable", "RULE_NO_EXECUTABLE"),
    ("unlisted", "RULE_MANIFEST_HASHES"),
    ("manifest", "RULE_MANIFEST"),
    ("changelog", "RULE_CHANGELOG"),
])
def test_pending_pack_refusals(tmp_path: Path, case: str, rule: str) -> None:
    root = repo(tmp_path)
    pack = root / "packs" / "example"
    if case == "symlink":
        (pack / "link").symlink_to("CHANGELOG.md")
    elif case == "dotfile":
        (pack / ".hidden").write_text("hidden")
    elif case == "scripts":
        (pack / "scripts").mkdir()
    elif case == "executable":
        (pack / "CHANGELOG.md").chmod(0o755)
    elif case == "unlisted":
        (pack / "extra.md").write_text("extra")
    elif case == "manifest":
        (pack / "manifest.yaml").unlink()
    else:
        (pack / "CHANGELOG.md").unlink()
    with pytest.raises(ValidationError, match=rule):
        build_index(root)


def test_two_stage_publication_and_update(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "index.json").write_text(index_text(root))
    git(root, "add", ".")
    git(root, "commit", "-qm", "Add pending pack")
    commit = git(root, "rev-parse", "HEAD")
    check(root)
    (root / "sources.yaml").write_text(f"sources: []\nown_pins:\n  - name: example\n    repo: cebertowicz/corsia-library\n    path: packs/example\n    commit: {commit}\n")
    text = index_text(root)
    row = json.loads(text)["skills"][0]
    assert row["source"]["commit"] == commit
    assert row["type"] == "corsia-blueprint"
    assert row["pack_kind"] == "blueprint"
    (root / "index.json").write_text(text)
    git(root, "add", ".")
    git(root, "commit", "-qm", "Publish pin and index")
    check(root)
    pack = root / "packs" / "example"
    manifest = pack / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("An example blueprint", "An updated blueprint").replace("1.0.0", "1.0.1"))
    changelog = pack / "CHANGELOG.md"
    changelog.write_text(changelog.read_text() + "\n1.0.1: Update.\n")
    import hashlib
    old_hash = "cd0210713777f77ad101610251b8dd61667a58d8e0386b92030b4e3d0ff60f81"
    manifest.write_text(manifest.read_text().replace(old_hash, hashlib.sha256(changelog.read_bytes()).hexdigest()))
    git(root, "add", ".")
    git(root, "commit", "-qm", "Update pending pack")
    assert index_text(root) == text
    check(root)
    subprocess.run([sys.executable, str(CHECK), "--root", str(root), "--base", "HEAD^"], check=True, capture_output=True, text=True)


def test_pin_must_be_ancestor(tmp_path: Path) -> None:
    root = repo(tmp_path)
    git(root, "add", ".")
    git(root, "commit", "-qm", "Add fixture")
    tree = git(root, "rev-parse", "HEAD^{tree}")
    unrelated = git(root, "commit-tree", tree, "-m", "Unrelated commit")
    (root / "sources.yaml").write_text(f"sources: []\nown_pins:\n  - name: example\n    repo: cebertowicz/corsia-library\n    path: packs/example\n    commit: {unrelated}\n")
    with pytest.raises(ValidationError, match="ancestor of HEAD"):
        build_index(root)


def pinned_skill_repo(root: Path) -> str:
    skill = root / "skills" / "example-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: example-skill\ndescription: An example.\nlicense: Apache-2.0\nmetadata:\n  version: 1.0.0\n---\n\nSteps.\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "item")
    return git(root, "rev-parse", "HEAD")


def pin_skill(root: Path, commit: str) -> None:
    (root / "sources.yaml").write_text(f"sources: []\nown_pins:\n  - name: example-skill\n    repo: cebertowicz/corsia-library\n    path: skills/example-skill\n    commit: \"{commit}\"\n")


def test_unpinned_skill_is_checked_but_not_indexed(tmp_path: Path) -> None:
    root = repo(tmp_path)
    pinned_skill_repo(root)
    assert build_index(root)["skills"] == []


def test_pinned_skill_row_has_a_pinned_source(tmp_path: Path) -> None:
    root = repo(tmp_path)
    commit = pinned_skill_repo(root)
    pin_skill(root, commit)
    row = build_index(root)["skills"][0]
    assert row["source"] == {"repository": "cebertowicz/corsia-library", "path": "skills/example-skill", "commit": commit}
    assert (row["kind"], row["url"], row["withdrawn"]) == ("skill", "skills/example-skill/SKILL.md", False)


def test_pinned_skill_serves_the_pinned_bytes_after_a_later_edit(tmp_path: Path) -> None:
    root = repo(tmp_path)
    commit = pinned_skill_repo(root)
    pin_skill(root, commit)
    before = build_index(root)["skills"][0]["digest"]
    skill = root / "skills" / "example-skill" / "SKILL.md"
    skill.write_text(skill.read_text().replace("1.0.0", "1.1.0") + "\nMore.\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "edit")
    assert build_index(root)["skills"][0]["digest"] == before
