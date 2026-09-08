# =============================================================================
# Stratum AI - Core Trust Engine Components
# =============================================================================
"""
Core components for the Trust-Gated Autopilot system.

- SignalHealthCalculator: Computes signal health from multiple sources
- TrustGate: Evaluates whether automation should proceed

The autopilot rules engine that used to be re-exported here was never
referenced from outside this package. The live automation path is
``app.autopilot.enforcer`` plus ``app.tasks.apply_actions_queue``, which use
``TrustGate``/``GateDecision`` directly.
"""

from app.stratum.core.signal_health import SignalHealthCalculator
from app.stratum.core.trust_gate import GateDecision, TrustGate, TrustGateResult

__all__ = [
    # Signal Health
    "SignalHealthCalculator",
    # Trust Gate
    "TrustGate",
    "TrustGateResult",
    "GateDecision",
]
