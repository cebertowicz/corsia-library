"""Validate browser step files before they enter the public library."""
from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from publicsuffixlist import PublicSuffixList

# A readable screen, not a semantic classifier; review still catches synonyms,
# obfuscation, and consequences hidden behind innocuous labels.
RISKY_WORDS = re.compile(
    r"\b(?:invit\w*|add[\s_-]*(?:a[\s_-]*)?(?:member|admin)\w*|"
    r"(?:add|make|grant|assign)[\w\s_-]{0,60}\badmin(?:istrator)?\b|admin[\s_-]+role[\s_-]+assignment|"
    r"shar(?:e|es|ed|ing)|token[\s_-]*(?:handoff|transfer|send|share)|"
    r"(?:send|share|transfer|copy|paste)[\s_-]*(?:a[\s_-]*)?token|"
    r"bill(?:ing)?\w*|delet\w*)\b", re.IGNORECASE
)
LITERAL_ADDRESS = re.compile(
    r"(?P<url>https?://[^\s\"'<>\\]+)|"
    r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?P<emailhost>[a-z0-9-]+(?:\.[a-z0-9-]+)+)|"
    r"\b(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\b|"
    r"\b(?P<barehost>(?:[\w-]+\.)+(?:xn--[a-z0-9-]+|[^\W\d_]{2,}))\b", re.IGNORECASE
)
PLACEHOLDER = re.compile(r"\$\{[^}]*\}")
CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# Browsers read a backslash as a slash and drop tabs and newlines; urlparse does not.
URL_BREAKER = re.compile(r"[\\\x00-\x20\x7f]")
SUFFIXES = PublicSuffixList()


def registrable_domain(host: str) -> str:
    # Keep in sync with backend/host_access/browser_domain.py in CORSIA.
    host = host.strip().rstrip(".").lower()
    if not host:
        return ""
    try:
        ipaddress.ip_address(host.strip("[]"))
        return host
    except ValueError:
        return SUFFIXES.privatesuffix(host) or host


def decoded(text: str) -> str:
    while (text_once_decoded := unquote(text)) != text:
        text = text_once_decoded
    return text


def ascii_host(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return host.lower()


def outside_address(value: str, domains: list[str]) -> bool:
    for match in LITERAL_ADDRESS.finditer(decoded(PLACEHOLDER.sub("", value))):
        rest = ""
        if match.group("url"):
            try:
                parsed = urlparse(match.group("url"))
                host = parsed.hostname
            except ValueError:
                return True
            rest = f"{parsed.path} {parsed.query} {parsed.fragment}"
        else:
            host = match.group("emailhost") or match.group("ip") or match.group("barehost")
        if host:
            host = ascii_host(host)
            known = match.group("barehost") is None or SUFFIXES.privatesuffix(host, accept_unknown=False)
            if known and registrable_domain(host) not in domains:
                return True
        if outside_address(rest, domains):
            return True
    return False


def risky(text: str) -> bool:
    words = CAMEL_BOUNDARY.sub(" ", decoded(PLACEHOLDER.sub("", text))).replace("_", " ")
    return bool(RISKY_WORDS.search(words))


def validate_steps(path: Path) -> None:
    def fail(rule: str, detail: str) -> None:
        raise ValueError(f"{rule}: {detail}")

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                fail("RULE_STEP_FORMAT", f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail("RULE_STEP_FORMAT", f"invalid JSON: {error}")
    if not isinstance(data, dict) or not {"domains", "steps"} <= data.keys() or data.keys() - {"domains", "steps", "note"}:
        fail("RULE_STEP_FORMAT", "expected domains, steps and optional note")
    domains, steps = data["domains"], data["steps"]
    if not isinstance(domains, list) or not domains or not all(isinstance(x, str) and x.strip() for x in domains):
        fail("RULE_STEP_DOMAIN", "domains must be a non-empty list")
    if any(SUFFIXES.privatesuffix(x, accept_unknown=False) != x for x in domains):
        fail("RULE_STEP_DOMAIN", "each domain must be a registrable domain on a known suffix")
    if not isinstance(steps, list) or not steps:
        fail("RULE_STEP_FORMAT", "steps must be a non-empty list")
    if "note" in data and not isinstance(data["note"], str):
        fail("RULE_STEP_FORMAT", "note must be text")
    if any(PLACEHOLDER.search(x) for x in domains) or PLACEHOLDER.search(data.get("note", "")):
        fail("RULE_STEP_FORMAT", "placeholders are allowed only in type values")
    ids: set[str] = set()
    allowed = {"goto": {"url", "reload"}, "click": {"selector"},
               "type": {"selector", "value"}, "wait_for": {"selector", "timeout", "ms"}}
    for step in steps:
        if not isinstance(step, dict) or not {"id", "says", "action"} <= step.keys():
            fail("RULE_STEP_FORMAT", "each step needs id, says and action")
        identifier, says, action = step["id"], step["says"], step["action"]
        if not isinstance(identifier, str) or not identifier or identifier in ids or not isinstance(says, str) or not says:
            fail("RULE_STEP_FORMAT", "each step needs a unique id and says")
        ids.add(identifier)
        if not isinstance(action, str) or action not in allowed:
            fail("RULE_STEP_FORMAT", "unknown action")
        if action == "click" and "script" in step:
            fail("RULE_STEP_SCRIPT", "click.script is refused")
        if step.keys() - {"id", "says", "action"} - allowed[action]:
            fail("RULE_STEP_FORMAT", "unknown step key")
        if any(PLACEHOLDER.search(value) for key, value in step.items() if isinstance(value, str) and not (action == "type" and key == "value")):
            fail("RULE_STEP_FORMAT", "placeholders are allowed only in type values")
        for key in ("says", "selector", "url", "value"):
            if isinstance(step.get(key), str) and risky(step[key]):
                fail("RULE_STEP_RISKY", f"step {identifier!r} contains a screened term")
        if action == "goto":
            keys = set(step) - {"id", "says", "action"}
            if keys == {"reload"} and step["reload"] is True:
                continue
            if keys != {"url"} or not isinstance(step.get("url"), str):
                fail("RULE_STEP_FORMAT", "goto needs url or reload")
            if URL_BREAKER.search(step["url"]):
                fail("RULE_STEP_DOMAIN", "goto url contains a backslash, space or control character")
            try:
                parsed = urlparse(step["url"])
                domain = registrable_domain(parsed.hostname or "")
            except ValueError:
                domain = ""
                parsed = None
            if not parsed or parsed.scheme not in {"http", "https"} or not domain or domain not in domains:
                fail("RULE_STEP_DOMAIN", "goto must use an allowed http(s) domain")
            if outside_address(step["url"], domains):
                fail("RULE_STEP_LITERAL", "goto url names an address outside the declared domains")
        elif action in {"click", "type"}:
            if not isinstance(step.get("selector"), str) or (action == "type" and not isinstance(step.get("value"), str)):
                fail("RULE_STEP_FORMAT", f"{action} needs selector and required value")
            if action == "type" and outside_address(step["value"], domains):
                fail("RULE_STEP_LITERAL", "type value contains an address outside the declared domains")
        else:
            keys = set(step) - {"id", "says", "action"}
            if keys == {"ms"}:
                valid = type(step["ms"]) is int and step["ms"] > 0
            else:
                valid = keys == {"selector", "timeout"} and isinstance(step.get("selector"), str) and type(step.get("timeout")) is int and step["timeout"] > 0
            if not valid:
                fail("RULE_STEP_FORMAT", "wait_for needs positive ms or selector and positive timeout")
