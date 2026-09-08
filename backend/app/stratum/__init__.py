# =============================================================================
# Stratum AI - Meta Platform Integration Module
# =============================================================================
"""
Stratum Meta Platform Integration.

Unified models and platform adapters for bi-directional sync with Meta
advertising channels (Facebook, Instagram, WhatsApp).

Module structure:
- models: unified data models
- core: signal health calculation and trust gate evaluation
- adapters: platform adapters (Meta, WhatsApp), resolved through
  ``adapters.registry``

Nothing outside this package imports ``app.stratum`` itself; every consumer
imports the submodule it needs. The re-exports below are kept deliberately
narrow for that reason - a name here is loaded by any import of any submodule,
because Python executes the parent package first.
"""

from app.stratum.core.signal_health import SignalHealthCalculator
from app.stratum.core.trust_gate import GateDecision, TrustGate, TrustGateResult
from app.stratum.models import (
    AutomationAction,
    BiddingStrategy,
    EMQScore,
    EntityStatus,
    PerformanceMetrics,
    Platform,
    SignalHealth,
    UnifiedAccount,
    UnifiedAd,
    UnifiedAdSet,
    UnifiedCampaign,
)

__all__ = [
    # Models
    "Platform",
    "EntityStatus",
    "BiddingStrategy",
    "UnifiedAccount",
    "UnifiedCampaign",
    "UnifiedAdSet",
    "UnifiedAd",
    "PerformanceMetrics",
    "EMQScore",
    "SignalHealth",
    "AutomationAction",
    # Core - Signal Health
    "SignalHealthCalculator",
    # Core - Trust Gate
    "TrustGate",
    "TrustGateResult",
    "GateDecision",
]
