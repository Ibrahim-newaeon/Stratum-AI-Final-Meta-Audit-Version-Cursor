"""Every mapped attribute in the model modules must carry a ``Mapped[...]``
annotation.

This is a merge guard, not a style preference. The models were converted from
``x = Column(...)`` to ``x: Mapped[T] = mapped_column(...)``, which let the
now-unused ``Column`` import be dropped from most modules. A branch that adds a
column the old way then merges *cleanly* with that conversion and produces a
module that raises on import:

    provider_metadata = Column(JSONB, nullable=True)
    NameError: name 'Column' is not defined

That happened twice while the conversion was in flight, and each time the whole
application failed to start - `import app.models` is on every path - while the
merge itself reported no conflict. Ruff does flag it as F821, but the Ruff step
is advisory and currently reports thousands of pre-existing findings, so the
one line that matters is invisible. This check is in the blocking test suite
and says exactly what to do.

The check is deliberately STATIC: it parses the source rather than importing
it, so it still reports the offending file and line when the module is the one
that cannot be imported.
"""

import ast
import pathlib

import pytest

# Located by path, NOT by importing app.models: the failure this guards against
# is precisely that app.models cannot be imported, and importing it here would
# turn a clear per-file assertion into a collection-time NameError that names no
# attribute and no line.
BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
MODELS_DIR = BACKEND_DIR / "app" / "models"
BASE_MODELS = BACKEND_DIR / "app" / "base_models.py"


def _model_modules() -> list[pathlib.Path]:
    """Every module that declares mapped classes."""
    paths = [
        p
        for p in sorted(MODELS_DIR.glob("*.py"))
        # historical duplicates are named "<name> 2.py"; the unsuffixed path is
        # canonical, and the duplicates are not imported by anything
        if p.stem != "__init__" and not p.stem.endswith(" 2")
    ]
    if BASE_MODELS.exists():
        paths.append(BASE_MODELS)
    return paths


def _legacy_column_assignments(path: pathlib.Path) -> list[tuple[str, str, int]]:
    """Class-level ``name = Column(...)`` assignments carrying no annotation.

    Returns (class name, attribute name, line number). An ``AnnAssign`` - the
    ``name: Mapped[T] = mapped_column(...)`` form - is not an ``ast.Assign``,
    so annotated attributes are skipped whatever they are assigned.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
                continue
            func = stmt.value.func
            called = (
                func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            )
            if called != "Column":
                continue
            found.extend(
                (node.name, t.id, stmt.lineno)
                for t in stmt.targets
                if isinstance(t, ast.Name)
            )
    return found


@pytest.mark.parametrize("module_path", _model_modules(), ids=lambda p: p.stem)
def test_model_attributes_use_mapped_column(module_path: pathlib.Path) -> None:
    """A mapped attribute declared with bare ``Column`` fails at import."""
    legacy = _legacy_column_assignments(module_path)
    assert not legacy, (
        f"{module_path.name} declares "
        + ", ".join(f"{cls}.{attr} (line {line})" for cls, attr, line in legacy)
        + " with `Column(...)`. These modules use SQLAlchemy 2.0 annotations and"
        " most no longer import `Column`, so this raises NameError on import and"
        " takes the whole app down. Write it as"
        " `attr: Mapped[T] = mapped_column(...)` instead - keep the same"
        " arguments, and make T optional exactly when the column is nullable."
    )
