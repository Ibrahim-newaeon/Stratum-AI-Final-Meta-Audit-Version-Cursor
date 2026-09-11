# =============================================================================
# Stratum AI - Rules → Meta policy (LOCAL_ONLY)
# =============================================================================
"""
Product policy: Automation Rules mutate **local** campaign rows only.

Meta Ads writes (pause, budget, bid, status on the live ad account) go
**only** through Autopilot:

    fact_actions_queue → trust gate → action_executor → write_client

Rules must never import or call ``write_client`` / ``action_executor``.
A future bridge may enqueue Autopilot queue rows from a rule; it still must
not POST to Meta from the rules path.

See ``docs/architecture/trust-engine.md`` ("Rules → Meta policy").
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings

# Stable scope tag stamped on every rule action_result for audit/UI.
EXECUTION_SCOPE_LOCAL_DB = "local_db_only"


class RulesMetaWriteForbidden(RuntimeError):
    """Raised if code attempts a Meta write from the rules path."""


def rules_may_write_meta() -> bool:
    """
    Whether Rules are allowed to cause Meta side effects.

    Default is False (LOCAL_ONLY). Even if an operator flips the env flag,
    callers must still not invoke ``write_client`` directly — only enqueue
    Autopilot actions (not implemented yet). This helper exists so any
    accidental Meta branch fails closed unless the flag is on *and* a
    dedicated bridge is built.
    """
    return bool(settings.rules_meta_writes_enabled)


def stamp_local_only(action_result: dict[str, Any]) -> dict[str, Any]:
    """Annotate a rule action result as local-DB-only (no Meta write)."""
    annotated = dict(action_result)
    annotated["execution_scope"] = EXECUTION_SCOPE_LOCAL_DB
    annotated["meta_write"] = False
    return annotated


def refuse_direct_meta_write(context: str) -> None:
    """
    Hard stop for any future Rules → Graph write attempt.

    Always refuses: Meta writes stay on the Autopilot path regardless of
    ``RULES_META_WRITES_ENABLED``. That flag only reserves room for an
    Autopilot-*queue* bridge, never for ``write_client`` from Rules.
    """
    raise RulesMetaWriteForbidden(
        f"Rules cannot write to Meta ({context}). "
        "Use Autopilot (fact_actions_queue → write_client) or keep LOCAL_ONLY "
        "local DB mutations. See docs/architecture/trust-engine.md."
    )
