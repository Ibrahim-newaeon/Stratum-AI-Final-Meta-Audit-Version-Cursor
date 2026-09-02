# =============================================================================
# Stratum AI - Root Onboarding Agent
# =============================================================================
"""
Rule-based conversational onboarding agent.

Guides a new user/tenant through onboarding via a deterministic state
machine: company info -> platform selection -> account connection ->
tracking -> thresholds -> automation -> review -> completed.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.services.agents.greeting_tool import UserContext, greeting_tool

logger = get_logger(__name__)

__all__ = [
    "ROOT_AGENT_INSTRUCTIONS",
    "AgentResponse",
    "ConversationContext",
    "ConversationState",
    "OnboardingData",
    "RootAgent",
    "root_agent",
]

ROOT_AGENT_INSTRUCTIONS = """
You are the Stratum AI onboarding assistant. Guide the user through:
1. Collecting company information
2. Selecting ad platforms (Meta: Facebook, Instagram, WhatsApp)
3. Connecting ad accounts
4. Configuring conversion tracking (Pixel / CAPI)
5. Setting the trust-gate threshold (default 70%)
6. Creating a first automation rule
7. Reviewing and launching
Be concise, friendly, and never skip trust-gate configuration.
""".strip()


class ConversationState(str, enum.Enum):
    """States of the onboarding conversation."""

    INITIAL = "initial"
    GREETING = "greeting"
    COLLECTING_COMPANY_INFO = "collecting_company_info"
    SELECTING_PLATFORMS = "selecting_platforms"
    CONNECTING_ACCOUNTS = "connecting_accounts"
    CONFIGURING_TRACKING = "configuring_tracking"
    SETTING_THRESHOLDS = "setting_thresholds"
    CREATING_AUTOMATION = "creating_automation"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    NEEDS_HELP = "needs_help"


_STATE_PROGRESS: dict[ConversationState, int] = {
    ConversationState.INITIAL: 0,
    ConversationState.GREETING: 0,
    ConversationState.COLLECTING_COMPANY_INFO: 20,
    ConversationState.SELECTING_PLATFORMS: 40,
    ConversationState.CONNECTING_ACCOUNTS: 50,
    ConversationState.CONFIGURING_TRACKING: 60,
    ConversationState.SETTING_THRESHOLDS: 75,
    ConversationState.CREATING_AUTOMATION: 85,
    ConversationState.REVIEWING: 95,
    ConversationState.COMPLETED: 100,
    ConversationState.NEEDS_HELP: 0,
}

_QUICK_REPLIES: dict[ConversationState, list[str]] = {
    ConversationState.GREETING: ["Get Started", "Learn More", "Watch Demo", "Talk to Sales"],
    ConversationState.COLLECTING_COMPANY_INFO: ["Skip"],
    ConversationState.SELECTING_PLATFORMS: ["meta", "Done"],
    ConversationState.CONNECTING_ACCOUNTS: ["Connect Now", "Skip for Later"],
    ConversationState.CONFIGURING_TRACKING: ["Yes", "Help me find them", "Skip"],
    ConversationState.SETTING_THRESHOLDS: ["Keep Default (70%)", "Adjust"],
    ConversationState.CREATING_AUTOMATION: ["Yes, create one", "Skip for now"],
    ConversationState.REVIEWING: ["Launch", "Make Changes"],
    ConversationState.COMPLETED: ["Go to Dashboard", "Create Automation", "Get Help"],
}


class OnboardingData(BaseModel):
    """Data collected during onboarding."""

    company_name: Optional[str] = None
    industry: Optional[str] = None
    platforms: list[str] = Field(default_factory=list)
    accounts_connected: bool = False
    tracking_configured: bool = False
    trust_threshold: int = 70
    automation_created: bool = False


class ConversationContext(BaseModel):
    """Serializable conversation state stored per session."""

    session_id: str
    state: ConversationState = ConversationState.GREETING
    user_context: UserContext = Field(default_factory=UserContext)
    onboarding_data: OnboardingData = Field(default_factory=OnboardingData)
    history: list[dict[str, str]] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentResponse(BaseModel):
    """Agent reply for a single conversational turn."""

    message: str
    state: ConversationState
    quick_replies: list[str] = Field(default_factory=list)
    progress_percent: int = 0
    next_step: Optional[str] = None
    requires_action: bool = False
    action_type: Optional[str] = None
    action_data: dict[str, Any] = Field(default_factory=dict)
    data_collected: dict[str, Any] = Field(default_factory=dict)


def _response_for(
    context: ConversationContext,
    message: str,
    requires_action: bool = False,
    action_type: Optional[str] = None,
    action_data: Optional[dict[str, Any]] = None,
) -> AgentResponse:
    """Build an AgentResponse from the current context state."""
    state = context.state
    return AgentResponse(
        message=message,
        state=state,
        quick_replies=list(_QUICK_REPLIES.get(state, [])),
        progress_percent=_STATE_PROGRESS.get(state, 0),
        next_step=_next_step_label(state),
        requires_action=requires_action,
        action_type=action_type,
        action_data=action_data or {},
        data_collected=context.onboarding_data.model_dump(),
    )


def _next_step_label(state: ConversationState) -> Optional[str]:
    """Human-readable label for the next step after the given state."""
    order = [
        ConversationState.GREETING,
        ConversationState.COLLECTING_COMPANY_INFO,
        ConversationState.SELECTING_PLATFORMS,
        ConversationState.CONNECTING_ACCOUNTS,
        ConversationState.CONFIGURING_TRACKING,
        ConversationState.SETTING_THRESHOLDS,
        ConversationState.CREATING_AUTOMATION,
        ConversationState.REVIEWING,
        ConversationState.COMPLETED,
    ]
    labels = {
        ConversationState.COLLECTING_COMPANY_INFO: "Tell us about your company",
        ConversationState.SELECTING_PLATFORMS: "Select your ad platforms",
        ConversationState.CONNECTING_ACCOUNTS: "Connect your ad accounts",
        ConversationState.CONFIGURING_TRACKING: "Configure conversion tracking",
        ConversationState.SETTING_THRESHOLDS: "Set your trust-gate threshold",
        ConversationState.CREATING_AUTOMATION: "Create your first automation",
        ConversationState.REVIEWING: "Review and launch",
        ConversationState.COMPLETED: None,
    }
    try:
        idx = order.index(state)
    except ValueError:
        return None
    if idx + 1 < len(order):
        return labels.get(order[idx + 1])
    return None


class RootAgent:
    """Deterministic state-machine onboarding agent."""

    async def start_conversation(
        self,
        user_context: UserContext,
        session_id: str,
    ) -> tuple[AgentResponse, ConversationContext]:
        """Start a new onboarding conversation; returns (response, context)."""
        context = ConversationContext(
            session_id=session_id,
            state=ConversationState.GREETING,
            user_context=user_context,
        )
        if user_context.company:
            context.onboarding_data.company_name = user_context.company

        greeting = greeting_tool.greet(user_context)
        response = _response_for(context, greeting.message)
        context.history.append({"role": "assistant", "content": response.message})
        return response, context

    async def process_message(
        self,
        message: str,
        context: ConversationContext,
    ) -> AgentResponse:
        """Process a user message, advance the state machine, and reply."""
        context.last_activity = datetime.now(UTC)
        context.history.append({"role": "user", "content": message})

        text = message.strip()
        lowered = text.lower()

        if "help" in lowered and context.state != ConversationState.NEEDS_HELP:
            response = self._help_response(context)
        else:
            handler = {
                ConversationState.INITIAL: self._handle_greeting,
                ConversationState.GREETING: self._handle_greeting,
                ConversationState.COLLECTING_COMPANY_INFO: self._handle_company_info,
                ConversationState.SELECTING_PLATFORMS: self._handle_platforms,
                ConversationState.CONNECTING_ACCOUNTS: self._handle_accounts,
                ConversationState.CONFIGURING_TRACKING: self._handle_tracking,
                ConversationState.SETTING_THRESHOLDS: self._handle_thresholds,
                ConversationState.CREATING_AUTOMATION: self._handle_automation,
                ConversationState.REVIEWING: self._handle_review,
                ConversationState.COMPLETED: self._handle_completed,
                ConversationState.NEEDS_HELP: self._handle_greeting,
            }[context.state]
            response = handler(context, text)

        context.history.append({"role": "assistant", "content": response.message})
        return response

    # -------------------------------------------------------------------
    # State handlers
    # -------------------------------------------------------------------

    def _help_response(self, context: ConversationContext) -> AgentResponse:
        return _response_for(
            context,
            "No problem - I'm here to help. You can continue the setup anytime, "
            "or reach our team at support@stratum.ai. Shall we continue?",
        )

    def _handle_greeting(self, context: ConversationContext, text: str) -> AgentResponse:
        context.state = ConversationState.COLLECTING_COMPANY_INFO
        return _response_for(
            context,
            "Great! First, what's your company name and industry? "
            "(You can also type Skip.)",
        )

    def _handle_company_info(self, context: ConversationContext, text: str) -> AgentResponse:
        if text.lower() != "skip":
            context.onboarding_data.company_name = text
        context.state = ConversationState.SELECTING_PLATFORMS
        return _response_for(
            context,
            "Which platforms do you advertise on? We support Meta "
            "(Facebook, Instagram and WhatsApp). Type 'meta' then 'Done'.",
        )

    def _handle_platforms(self, context: ConversationContext, text: str) -> AgentResponse:
        lowered = text.lower()
        if lowered in {"meta", "facebook", "instagram", "whatsapp"}:
            if "meta" not in context.onboarding_data.platforms:
                context.onboarding_data.platforms.append("meta")
            return _response_for(
                context,
                "Meta added. Type 'Done' when you're finished selecting platforms.",
            )
        context.state = ConversationState.CONNECTING_ACCOUNTS
        return _response_for(
            context,
            "Now let's connect your ad accounts (read-only access). "
            "Ready to connect?",
            requires_action=True,
            action_type="connect_accounts",
            action_data={"platforms": context.onboarding_data.platforms or ["meta"]},
        )

    def _handle_accounts(self, context: ConversationContext, text: str) -> AgentResponse:
        context.onboarding_data.accounts_connected = "skip" not in text.lower()
        context.state = ConversationState.CONFIGURING_TRACKING
        return _response_for(
            context,
            "Do you have the Meta Pixel and Conversions API set up for "
            "conversion tracking?",
        )

    def _handle_tracking(self, context: ConversationContext, text: str) -> AgentResponse:
        context.onboarding_data.tracking_configured = text.lower().startswith("yes")
        context.state = ConversationState.SETTING_THRESHOLDS
        return _response_for(
            context,
            "Autopilot only executes actions when signal health passes the trust "
            "gate. The recommended threshold is 70%. Keep the default or adjust?",
        )

    def _handle_thresholds(self, context: ConversationContext, text: str) -> AgentResponse:
        digits = "".join(c for c in text if c.isdigit())
        if digits and "adjust" not in text.lower():
            context.onboarding_data.trust_threshold = max(40, min(int(digits), 100))
        context.state = ConversationState.CREATING_AUTOMATION
        return _response_for(
            context,
            f"Trust threshold set to {context.onboarding_data.trust_threshold}%. "
            "Want to create your first automation rule now?",
        )

    def _handle_automation(self, context: ConversationContext, text: str) -> AgentResponse:
        context.onboarding_data.automation_created = text.lower().startswith("yes")
        context.state = ConversationState.REVIEWING
        data = context.onboarding_data
        summary = (
            f"Here's your setup:\n"
            f"- Company: {data.company_name or 'Not provided'}\n"
            f"- Platforms: {', '.join(data.platforms) or 'meta'}\n"
            f"- Accounts connected: {'yes' if data.accounts_connected else 'later'}\n"
            f"- Tracking configured: {'yes' if data.tracking_configured else 'later'}\n"
            f"- Trust threshold: {data.trust_threshold}%\n"
            f"- First automation: {'yes' if data.automation_created else 'later'}\n\n"
            "Ready to launch?"
        )
        return _response_for(context, summary)

    def _handle_review(self, context: ConversationContext, text: str) -> AgentResponse:
        if "change" in text.lower():
            context.state = ConversationState.COLLECTING_COMPANY_INFO
            return _response_for(
                context,
                "Sure - let's go through it again. What's your company name?",
            )
        context.state = ConversationState.COMPLETED
        return _response_for(
            context,
            "You're all set! Your Stratum AI workspace is ready. "
            "Head to the dashboard to see your campaigns.",
            requires_action=True,
            action_type="complete_onboarding",
        )

    def _handle_completed(self, context: ConversationContext, text: str) -> AgentResponse:
        return _response_for(
            context,
            "Onboarding is complete. You can go to your dashboard or ask me "
            "for help anytime.",
        )


# Shared singleton instance
root_agent = RootAgent()
