"""Every table-declaring model module must be imported by ``app.models``.

`app/models/capi_delivery.py` declared four tables but was never imported by
`app.models`. The baseline builds the schema with `create_all` over that
metadata, so the tables were never created in any deployed database. Nothing
noticed while the CAPI path appended to an in-memory list; the moment signal
health read `capi_delivery_logs` for real, the dashboard returned 500.

These checks are deliberately STATIC - they parse `app/models/__init__.py`
rather than inspecting `Base.metadata`. Runtime metadata is shared process
state that any earlier test can populate by importing a module directly, which
would let an unregistered module look registered and hide exactly this bug.
"""

import ast
import pathlib

import pytest

import app.models

MODELS_DIR = pathlib.Path(app.models.__file__).parent
INIT_PATH = MODELS_DIR / "__init__.py"

# Modules deliberately left unregistered, each with the reason. These are debt,
# not design: every table they declare is missing from every database.
# Registering either today raises InvalidRequestError, because both redeclare a
# table another module already owns (for example `competitor_benchmarks`).
# Untangling those duplicates is its own change.
# Both former entries are gone: audit_services no longer redeclares
# competitor_benchmarks (its model was renamed to IndustryBenchmark on
# industry_benchmarks) and both modules are now registered with migrations
# creating their tables. Empty is the goal state - add an entry only with a
# reason, and delete it the moment the module is wired up.
KNOWN_UNREGISTERED: dict[str, str] = {}


def _declared_tables(path: pathlib.Path) -> set[str]:
    """Return every ``__tablename__`` string literal assigned in one module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and any(
            isinstance(t, ast.Name) and t.id == "__tablename__" for t in node.targets
        )
    }


def _imported_modules() -> set[str]:
    """Return the ``app.models.<name>`` modules that ``__init__`` imports."""
    tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"))
    return {
        node.module.split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith("app.models.")
    }


def _table_declaring_modules() -> list[pathlib.Path]:
    """Every module under app/models that declares at least one table."""
    return sorted(
        p
        for p in MODELS_DIR.glob("*.py")
        if p.stem != "__init__" and not p.stem.endswith(" 2") and _declared_tables(p)
    )


@pytest.mark.parametrize(
    "module_path", _table_declaring_modules(), ids=lambda p: p.stem
)
def test_table_declaring_module_is_imported_by_app_models(
    module_path: pathlib.Path,
) -> None:
    """A module declaring tables must be imported, or Alembic never sees them."""
    if module_path.stem in KNOWN_UNREGISTERED:
        pytest.skip(f"known debt: {KNOWN_UNREGISTERED[module_path.stem]}")

    assert module_path.stem in _imported_modules(), (
        f"{module_path.name} declares {sorted(_declared_tables(module_path))} but "
        f"app/models/__init__.py does not import it, so create_all never builds "
        f"those tables. Import it there and add an Alembic revision creating them "
        f"for databases that already ran the baseline."
    )


def test_capi_delivery_is_imported() -> None:
    """The specific regression: these four tables returned 500s in production."""
    assert "capi_delivery" in _imported_modules()


def test_known_unregistered_list_stays_honest() -> None:
    """An entry on the debt list must still actually be unimported.

    Once someone wires one up this fails, forcing the entry to be removed, so
    the list cannot rot into a permanent excuse.
    """
    imported = _imported_modules()
    for stem, reason in KNOWN_UNREGISTERED.items():
        if not (MODELS_DIR / f"{stem}.py").exists():
            continue
        assert stem not in imported, (
            f"{stem} is now imported by app.models ({reason}); "
            f"remove it from KNOWN_UNREGISTERED"
        )
