# =============================================================================
# Stratum AI - Meta Platform Integration Module
# =============================================================================
"""
Stratum Meta Platform Integration.

Provides unified models and platform adapters for bi-directional sync
with Meta advertising channels (Facebook, Instagram, WhatsApp).

Key Components:
- Unified data models for Meta channel compatibility
- Platform adapters with full CRUD operations
- Signal health calculator with 4 components
- Trust gate evaluation for automation decisions
- Autopilot engine with built-in rules
- Server-side events tracking (CAPI)
- Webhook server for real-time updates
- Celery workers for data sync and automation

Module Structure:
- models: Unified data models
- core: Signal health, trust gate, autopilot
- adapters: Platform adapters (Meta, WhatsApp)
- workers: Celery tasks (data_sync, automation_runner)
- conversions: Conversions API clients
- events: Full-funnel server-side events
- webhooks: FastAPI webhook server
"""

# Models
# Conversions API
from app.stratum.conversions import (
    ConversionEvent,
    EventType,
    MetaConversionsAPI,
    UnifiedConversionsAPI,
    UserData as ConversionUserData,
)

# Core - Autopilot
from app.stratum.core.autopilot import (
    AutopilotEngine,
    AutopilotRule,
    BudgetPacingRule,
    PerformanceScalingRule,
    RuleContext,
    RuleType,
    StatusManagementRule,
    TrustGatedAutopilot,
)

# Core - Signal Health & Trust Gate
from app.stratum.core.signal_health import SignalHealthCalculator
from app.stratum.core.trust_gate import GateDecision, TrustGate, TrustGateResult

# Full-funnel Events
from app.stratum.events import (
    EVENT_MAPPING,
    ContentItem,
    EcommerceTracker,
    MetaEventsSender,
    ServerEvent,
    StandardEvent,
    UnifiedEventsAPI,
    UserData as EventUserData,
)

# MCP Server
from app.stratum.mcp import (
    MCP_TOOLS,
    StratumMCPServer,
    create_mcp_server,
)
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
    # Core - Autopilot
    "RuleType",
    "RuleContext",
    "AutopilotRule",
    "BudgetPacingRule",
    "PerformanceScalingRule",
    "StatusManagementRule",
    "AutopilotEngine",
    "TrustGatedAutopilot",
    # Conversions API
    "EventType",
    "ConversionUserData",
    "ConversionEvent",
    "MetaConversionsAPI",
    "UnifiedConversionsAPI",
    # Full-funnel Events
    "StandardEvent",
    "EVENT_MAPPING",
    "EventUserData",
    "ContentItem",
    "ServerEvent",
    "MetaEventsSender",
    "UnifiedEventsAPI",
    "EcommerceTracker",
    # MCP Server
    "StratumMCPServer",
    "MCP_TOOLS",
    "create_mcp_server",
]
