"""measures.yaml is read with YAML 1.2 booleans: only true and false are booleans.

A YAML 1.1 round trip (a plain PyYAML load and dump) turns the ``on:`` join key into
``true:`` and ``alert: off`` into ``alert: false``. That file no longer syncs, so this test
reads it the same way the measure loader does and checks those keys and values survive.
"""
from pathlib import Path
import re

import yaml

MEASURES = Path(__file__).parents[2] / "measures.yaml"
_BOOL = "tag:yaml.org,2002:bool"


class _Yaml12(yaml.SafeLoader):
    pass


_Yaml12.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag != _BOOL]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Yaml12.add_implicit_resolver(_BOOL, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def _mappings(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _mappings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _mappings(value)


def _data():
    return yaml.load(MEASURES.read_text(), Loader=_Yaml12)  # noqa: S506 - a SafeLoader subclass


def test_every_key_is_a_string_and_none_is_a_boolean_word():
    for mapping in _mappings(_data()):
        for key in mapping:
            assert isinstance(key, str) and key not in {"true", "false"}, key


def test_match_clauses_and_corroboration_join_on_a_named_attribute():
    data = _data()
    clauses = [clause for kpi in data["kpis"].values() for side in ("num", "den")
               for clause in (kpi.get(side) or {}).get("match", [])]
    assert clauses
    for clause in clauses:
        assert isinstance(clause.get("on"), str), clause
    for event in data["events"].values():
        corroborate = event.get("corroborate")
        if isinstance(corroborate, dict) and "platform_event" in corroborate:
            assert isinstance(corroborate["platform_event"].get("on"), str), corroborate


def test_alert_is_a_named_mode_not_a_boolean():
    for kpi in _data()["kpis"].values():
        if "alert" in kpi:
            assert isinstance(kpi["alert"], str), kpi["alert"]
