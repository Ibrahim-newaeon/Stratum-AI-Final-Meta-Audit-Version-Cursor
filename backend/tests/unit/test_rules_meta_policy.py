"""Rules → Meta policy: LOCAL_ONLY by default; no write_client from Rules."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.rules_meta_policy import (
    EXECUTION_SCOPE_LOCAL_DB,
    RulesMetaWriteForbidden,
    refuse_direct_meta_write,
    rules_may_write_meta,
    stamp_local_only,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
RULES_MODULES = (
    BACKEND_ROOT / "app" / "workers" / "tasks" / "rules.py",
    BACKEND_ROOT / "app" / "services" / "rules_engine.py",
)

FORBIDDEN_IMPORT_SUFFIXES = (
    "services.meta.write_client",
    "services.meta.action_executor",
    "app.services.meta.write_client",
    "app.services.meta.action_executor",
)


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            for alias in node.names:
                names.add(f"{node.module}.{alias.name}")
    return names


def test_rules_meta_writes_disabled_by_default():
    assert settings.rules_meta_writes_enabled is False
    assert rules_may_write_meta() is False


def test_stamp_local_only_marks_scope():
    stamped = stamp_local_only({"action": "pause_campaign", "success": True})
    assert stamped["execution_scope"] == EXECUTION_SCOPE_LOCAL_DB
    assert stamped["meta_write"] is False
    assert stamped["action"] == "pause_campaign"


def test_refuse_direct_meta_write_always():
    with pytest.raises(RulesMetaWriteForbidden, match="cannot write to Meta"):
        refuse_direct_meta_write("unit-test")


@pytest.mark.parametrize("path", RULES_MODULES, ids=lambda p: p.name)
def test_rules_modules_do_not_import_meta_writers(path: Path):
    assert path.is_file(), f"missing {path}"
    imported = _imported_names(path)
    offenders = {
        name
        for name in imported
        if any(name == bad or name.endswith(bad) for bad in FORBIDDEN_IMPORT_SUFFIXES)
    }
    assert not offenders, f"{path.name} must not import Meta writers: {offenders}"
