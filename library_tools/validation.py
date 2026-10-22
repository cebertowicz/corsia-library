"""Generate the discovery index and enforce the public library envelope."""
from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import skills_ref
import yaml

SCHEMA_URL = "https://schemas.agentskills.io/discovery/0.2.0/schema.json"
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:[-+][0-9A-Za-z.-]+)?$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
NAME_FORMAT = re.compile(r"^[a-z][a-z0-9-]*$")
CLAIM_WORDS = ("default", "official", "recommended")
ALLOWED_LICENSES = {"Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause"}
FLOW_YAML = re.compile(r"^(?:\s*-\s*|[^:#\n]*:\s*)[\[{]", re.MULTILINE)
ASSET_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".svg", ".ttf", ".woff", ".woff2"}
PRIVATE_MARKERS = ("BEGIN CORSIA CONTEXT", "AGENT-SEND", "CORSIA CONTEXT SOURCES")
PUBLIC_PATTERNS = {
    "absolute home path": re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home)/[^\s\"'<>`]+"),
    "email address": re.compile(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+\b"),
    "credential-like string": re.compile(r"(?:gh[pous]_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|(?:api[_-]?key|token|password|secret)\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{8,})", re.IGNORECASE),
}


@dataclass(frozen=True)
class ValidationError(Exception):
    rule: str
    item: str
    file: str
    detail: str

    def __str__(self) -> str:
        return f"{self.rule}: item '{self.item}', file '{self.file}': {self.detail}"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def files(root: Path) -> list[Path]:
    return sorted((path for path in root.rglob("*") if path.is_file() or path.is_symlink()), key=lambda path: path.as_posix())


def load_mapping(path: Path, item: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ValidationError("RULE_METADATA", item, str(path), f"invalid YAML: {error}") from error
    if not isinstance(value, dict):
        raise ValidationError("RULE_METADATA", item, str(path), "expected a mapping")
    return value


def frontmatter(path: Path, item: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValidationError("RULE_FRONTMATTER", item, "SKILL.md", "missing YAML frontmatter")
    try:
        _, raw, _ = text.split("---\n", 2)
        value = yaml.safe_load(raw) or {}
    except (ValueError, yaml.YAMLError) as error:
        raise ValidationError("RULE_FRONTMATTER", item, "SKILL.md", f"invalid YAML: {error}") from error
    if not isinstance(value, dict):
        raise ValidationError("RULE_FRONTMATTER", item, "SKILL.md", "expected a mapping")
    return value


def public_text(text: str, item: str, filename: str) -> None:
    for marker in PRIVATE_MARKERS:
        if marker in text:
            raise ValidationError("RULE_PUBLIC_CONTENT", item, filename, f"contains private marker {marker!r}")
    for label, pattern in PUBLIC_PATTERNS.items():
        if pattern.search(text):
            raise ValidationError("RULE_PUBLIC_CONTENT", item, filename, f"contains {label}")


def safe_item(root: Path, item: str) -> None:
    for directory in sorted(path for path in root.rglob("*") if path.is_dir()):
        if directory.name == "scripts":
            raise ValidationError("RULE_NO_SCRIPTS", item, rel(root, directory) + "/", "scripts are refused until sandbox checks exist")
    for path in files(root):
        filename = rel(root, path)
        parts = PurePosixPath(filename).parts
        if path.is_symlink():
            raise ValidationError("RULE_NO_SYMLINK", item, filename, "symlinks are refused")
        if any(part.startswith(".") for part in parts):
            raise ValidationError("RULE_NO_DOTFILE", item, filename, "dotfiles are refused")
        info = path.stat()
        if info.st_nlink > 1:
            raise ValidationError("RULE_NO_HARDLINK", item, filename, "hard links are refused")
        if stat.S_IMODE(info.st_mode) & 0o111:
            raise ValidationError("RULE_NO_EXECUTABLE", item, filename, "executable files are refused")
        if "scripts" in parts:
            raise ValidationError("RULE_NO_SCRIPTS", item, filename, "scripts are refused until sandbox checks exist")
        data = path.read_bytes()
        try:
            public_text(data.decode("utf-8"), item, filename)
        except UnicodeDecodeError:
            if not (parts[0] == "assets" and path.suffix.lower() in ASSET_SUFFIXES):
                raise ValidationError("RULE_BINARY", item, filename, "binary file is not an approved asset")


def version(value: Any, item: str, filename: str) -> str:
    if not isinstance(value, str) or not SEMVER.fullmatch(value):
        raise ValidationError("RULE_VERSION", item, filename, "version must be semver")
    return value


def skill_basics(root: Path, item: str) -> dict[str, Any]:
    skill = root / "SKILL.md"
    if not skill.is_file():
        raise ValidationError("RULE_SKILL_FILE", item, "SKILL.md", "uppercase SKILL.md is required")
    safe_item(root, item)
    data = frontmatter(skill, item)
    raw_frontmatter = skill.read_text(encoding="utf-8").split("---\n", 2)[1]
    if FLOW_YAML.search(raw_frontmatter):
        raise ValidationError("RULE_NO_FLOW_YAML", item, "SKILL.md", "flow collections are refused")
    if data.get("name") != root.name:
        raise ValidationError("RULE_SKILL_NAME", item, "SKILL.md", "name must match the directory")
    if not isinstance(data.get("description"), str) or not data["description"].strip():
        raise ValidationError("RULE_DESCRIPTION", item, "SKILL.md", "description is required")
    return data


def validate_skill(root: Path) -> dict[str, Any]:
    item = f"skills/{root.name}"
    data = skill_basics(root, item)
    metadata = data.get("metadata")
    if not isinstance(metadata, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in metadata.items()):
        raise ValidationError("RULE_METADATA", item, "SKILL.md", "metadata must be a string map")
    data["version"] = version(metadata.get("version"), item, "SKILL.md")
    return data


def validate_pack(root: Path) -> dict[str, Any]:
    item = f"packs/{root.name}"
    manifest = root / "manifest.yaml"
    if not manifest.is_file():
        raise ValidationError("RULE_MANIFEST", item, "manifest.yaml", "manifest.yaml is required")
    if not (root / "CHANGELOG.md").is_file():
        raise ValidationError("RULE_CHANGELOG", item, "CHANGELOG.md", "CHANGELOG.md is required")
    safe_item(root, item)
    data = load_mapping(manifest, item)
    if data.get("schema") != "corsia.pack/1":
        raise ValidationError("RULE_PACK_SCHEMA", item, "manifest.yaml", "schema must be corsia.pack/1")
    if data.get("name") != root.name:
        raise ValidationError("RULE_PACK_NAME", item, "manifest.yaml", "name must match the directory")
    data["version"] = version(data.get("version"), item, "manifest.yaml")
    if data.get("kind") not in {"blueprint", "connector"}:
        raise ValidationError("RULE_PACK_KIND", item, "manifest.yaml", "kind must be blueprint or connector")
    if data["kind"] == "blueprint" and (not data.get("fits") or not data.get("requires")):
        raise ValidationError("RULE_BLUEPRINT_FIT", item, "manifest.yaml", "blueprints require fits and requires")
    declared_files = data.get("files")
    if declared_files is not None:
        actual_files = [entry for entry in file_entries(root) if entry["path"] != "manifest.yaml"]
        if declared_files != actual_files:
            raise ValidationError("RULE_MANIFEST_HASHES", item, "manifest.yaml", "files must match every non-manifest file")
    return data


def sources(root: Path) -> list[dict[str, Any]]:
    data = load_mapping(root / "sources.yaml", "sources")
    entries = data.get("sources", [])
    if not isinstance(entries, list):
        raise ValidationError("RULE_SOURCES", "sources", "sources.yaml", "sources must be a list")
    for position, entry in enumerate(entries):
        name = entry.get("name", f"sources[{position}]") if isinstance(entry, dict) else f"sources[{position}]"
        if not isinstance(entry, dict):
            raise ValidationError("RULE_SOURCES", "sources", name, "source must be a mapping")
        if not isinstance(entry.get("commit"), str) or not FULL_SHA.fullmatch(entry["commit"]):
            raise ValidationError("RULE_PIN_SHA", name, "sources.yaml", "commit must be a full 40-character SHA")
        if entry.get("license") not in ALLOWED_LICENSES:
            raise ValidationError("RULE_LICENSE", name, "sources.yaml", "license is not on the allowlist")
        if not entry.get("reviewed_by"):
            raise ValidationError("RULE_REVIEWER", name, "sources.yaml", "reviewed_by is required")
        if not isinstance(entry.get("digest"), str) or not SHA256.fullmatch(entry["digest"]):
            raise ValidationError("RULE_PIN_DIGEST", name, "sources.yaml", "digest must be sha256:<64 lowercase hex>")
        path = entry.get("path")
        if not isinstance(path, str) or not path or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise ValidationError("RULE_SOURCE_PATH", name, "sources.yaml", "path must stay inside the pinned repository")
        # Outside skills rarely carry metadata.version, so the reviewed pin row owns it.
        version(entry.get("version"), name, "sources.yaml")
    return entries


def file_entries(root: Path) -> list[dict[str, str]]:
    return [{"path": rel(root, path), "sha256": f"sha256:{sha256_file(path)}"} for path in files(root)]


def folder_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for entry in file_entries(root):
        digest.update(entry["path"].encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(entry["sha256"].split(":", 1)[1]))
    return f"sha256:{digest.hexdigest()}"


def skill_entry(root: Path) -> dict[str, Any]:
    data = validate_skill(root)
    return {"name": data["name"], "description": data["description"], "type": "skill-md", "url": f"skills/{root.name}/SKILL.md", "digest": f"sha256:{sha256_file(root / 'SKILL.md')}", "version": data["version"], "kind": "skill", "license": data.get("license", "Apache-2.0"), "source": "library", "files": file_entries(root), "withdrawn": False, "withdrawn_reason": None}


def pack_entry(root: Path) -> dict[str, Any]:
    data = validate_pack(root)
    return {"name": data["name"], "description": data.get("summary", ""), "type": "archive", "url": f"packs/{root.name}/", "digest": folder_digest(root), "version": data["version"], "kind": "pack", "license": data.get("license", "Apache-2.0"), "source": "library", "files": file_entries(root), "withdrawn": False, "withdrawn_reason": None}


def checkout_pin(entry: dict[str, Any], destination: Path) -> Path:
    """Fetch exactly the reviewed source commit, never a moving branch."""
    name = entry.get("name", "source")
    repository = entry.get("repo")
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValidationError("RULE_SOURCE_REPO", name, "sources.yaml", "repo must be owner/name")
    subprocess.run(["git", "clone", "--no-checkout", f"https://github.com/{repository}.git", str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "fetch", "--depth", "1", "origin", entry["commit"]], check=True)
    subprocess.run(["git", "-C", str(destination), "checkout", "--detach", entry["commit"]], check=True)
    source = destination / PurePosixPath(entry["path"])
    if not source.is_dir():
        raise ValidationError("RULE_SOURCE_PATH", name, entry["path"], "pinned path is not a directory")
    return source


def outside_entry(entry: dict[str, Any], source: Path) -> dict[str, Any]:
    data = skill_basics(source, entry["name"])
    # CI runs the Agent Skills checker on skills/; pinned folders exist only here.
    problems = skills_ref.validate(source)
    if problems:
        raise ValidationError("RULE_AGENT_SKILLS", entry["name"], "SKILL.md", "; ".join(problems))
    digest = f"sha256:{sha256_file(source / 'SKILL.md')}"
    if digest != entry["digest"]:
        raise ValidationError("RULE_PIN_DIGEST", entry["name"], "SKILL.md", "declared digest does not match the pinned source")
    return {"name": data["name"], "description": data["description"], "type": "skill-md", "url": f"https://github.com/{entry['repo']}/tree/{entry['commit']}/{entry['path']}/SKILL.md", "digest": digest, "version": entry["version"], "kind": "skill", "license": entry["license"], "source": {"repository": entry["repo"], "path": entry["path"], "commit": entry["commit"]}, "files": file_entries(source), "withdrawn": bool(entry.get("withdrawn", False)), "withdrawn_reason": entry.get("withdrawn_reason")}


def reserved_names(root: Path) -> set[str]:
    path = root / "reserved-names.yaml"
    if not path.is_file():
        raise ValidationError("RULE_RESERVED_FILE", "reserved-names", "reserved-names.yaml", "reserved-names.yaml is required")
    names = load_mapping(path, "reserved-names").get("reserved")
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ValidationError("RULE_RESERVED_FILE", "reserved-names", "reserved-names.yaml", "reserved must be a list of names")
    return {name.lower() for name in names}


def declared_names(root: Path) -> list[tuple[Any, str, str]]:
    """Every item name as (name, holder, file): skill frontmatter, pack manifest, pin row."""
    found = []
    for kind, filename in (("skills", "SKILL.md"), ("packs", "manifest.yaml")):
        directory = root / kind
        for child in sorted(directory.iterdir()) if directory.is_dir() else []:
            path = child / filename
            if path.is_file():
                item = f"{kind}/{child.name}"
                data = frontmatter(path, item) if kind == "skills" else load_mapping(path, item)
                found.append((data.get("name"), item, filename))
    pins = load_mapping(root / "sources.yaml", "sources").get("sources", [])
    for position, pin in enumerate(pins if isinstance(pins, list) else []):
        if isinstance(pin, dict):
            found.append((pin.get("name"), f"pins/{pin.get('name', position)}", "sources.yaml"))
    return found


def check_names(root: Path) -> None:
    reserved = reserved_names(root)
    holders: dict[str, str] = {}
    for name, item, filename in declared_names(root):
        if not isinstance(name, str) or not NAME_FORMAT.fullmatch(name):
            raise ValidationError("RULE_NAME_FORMAT", item, filename, f"name {name!r} must match {NAME_FORMAT.pattern}")
        key = name.lower()
        if key in reserved:
            raise ValidationError("RULE_NAME_RESERVED", item, filename, f"name '{name}' is reserved by the CORSIA platform (reserved-names.yaml)")
        if any(word in key for word in CLAIM_WORDS):
            raise ValidationError("RULE_NAME_CLAIM", item, filename, f"name '{name}' may not contain {', '.join(CLAIM_WORDS)}")
        if key in holders:
            raise ValidationError("RULE_NAME_HELD", item, filename, f"name '{name}' is already held by {holders[key]}")
        holders[key] = item


def check_names_kept(root: Path, base: str) -> None:
    """Items are withdrawn, never deleted: a name in the base index stays in the index for good."""
    old = subprocess.run(["git", "show", f"{base}:index.json"], cwd=root, capture_output=True, text=True)
    if old.returncode:
        return
    kept = {entry["name"].lower() for entry in json.loads((root / "index.json").read_text(encoding="utf-8"))["skills"]}
    for entry in json.loads(old.stdout)["skills"]:
        if entry["name"].lower() not in kept:
            raise ValidationError("RULE_NAME_HELD", entry["name"], "index.json", f"name '{entry['name']}' was in the base index and cannot leave it; withdraw the item instead of deleting it")


def build_index(root: Path) -> dict[str, Any]:
    check_names(root)
    entries = []
    for directory, make_entry in ((root / "skills", skill_entry), (root / "packs", pack_entry)):
        if directory.is_dir():
            entries.extend(make_entry(child) for child in sorted(directory.iterdir()) if child.is_dir())
    for entry in sources(root):
        with tempfile.TemporaryDirectory() as temporary:
            entries.append(outside_entry(entry, checkout_pin(entry, Path(temporary) / "source")))
    return {"$schema": SCHEMA_URL, "skills": sorted(entries, key=lambda item: item["name"])}


def index_text(root: Path) -> str:
    return json.dumps(build_index(root), indent=2) + "\n"


def validate_repository(root: Path) -> None:
    build_index(root)


def check_version_bumps(root: Path, base: str) -> None:
    result = subprocess.run(["git", "diff", "--name-only", base, "HEAD", "--", "skills", "packs"], cwd=root, check=True, capture_output=True, text=True)
    items = {Path(*Path(line).parts[:2]) for line in result.stdout.splitlines() if len(Path(line).parts) >= 2}
    for item in items:
        current = root / item
        if not current.is_dir():
            continue
        data = validate_skill(current) if item.parts[0] == "skills" else validate_pack(current)
        changelog = current / "CHANGELOG.md"
        if not changelog.is_file() or data["version"] not in changelog.read_text(encoding="utf-8"):
            raise ValidationError("RULE_CHANGELOG", item.as_posix(), "CHANGELOG.md", "changed item needs an entry for its version")
        filename = "SKILL.md" if item.parts[0] == "skills" else "manifest.yaml"
        old = subprocess.run(["git", "show", f"{base}:{item.as_posix()}/{filename}"], cwd=root, capture_output=True, text=True)
        if old.returncode:
            continue
        old_data = frontmatter_text(old.stdout, item.as_posix(), filename) if filename == "SKILL.md" else yaml.safe_load(old.stdout)
        old_version = old_data.get("metadata", {}).get("version") if filename == "SKILL.md" else old_data.get("version")
        if not isinstance(old_version, str) or semver_key(data["version"]) <= semver_key(old_version):
            raise ValidationError("RULE_VERSION_BUMP", item.as_posix(), filename, "changed item must raise its version")


def frontmatter_text(text: str, item: str, filename: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        raise ValidationError("RULE_FRONTMATTER", item, filename, "missing YAML frontmatter")
    try:
        _, raw, _ = text.split("---\n", 2)
        data = yaml.safe_load(raw) or {}
    except (ValueError, yaml.YAMLError) as error:
        raise ValidationError("RULE_FRONTMATTER", item, filename, f"invalid YAML: {error}") from error
    if not isinstance(data, dict):
        raise ValidationError("RULE_FRONTMATTER", item, filename, "expected a mapping")
    return data


def semver_key(value: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(value)
    if not match:
        return (-1, -1, -1)
    return tuple(int(part) for part in match.group(1, 2, 3))


def validate_version_change(current_root: Path, prior_root: Path, item: Path) -> None:
    current = current_root / item
    prior = prior_root / item
    current_data = validate_skill(current) if item.parts[0] == "skills" else validate_pack(current)
    prior_data = validate_skill(prior) if item.parts[0] == "skills" else validate_pack(prior)
    filename = "SKILL.md" if item.parts[0] == "skills" else "manifest.yaml"
    if current_data["version"] == prior_data["version"]:
        raise ValidationError("RULE_VERSION_BUMP", item.as_posix(), filename, "changed item must raise its version")
    changelog = current / "CHANGELOG.md"
    if not changelog.is_file() or current_data["version"] not in changelog.read_text(encoding="utf-8"):
        raise ValidationError("RULE_CHANGELOG", item.as_posix(), "CHANGELOG.md", "changed item needs an entry for its version")
