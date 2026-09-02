# =============================================================================
# Stratum AI - Onboarding Greeting Tool
# =============================================================================
"""
Greeting tool for the conversational onboarding agent.

Builds a localized, context-aware greeting for new and returning users.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Optional

from pydantic import BaseModel, Field

__all__ = [
    "GreetingResponse",
    "GreetingTool",
    "GreetingType",
    "UserContext",
    "greet_user",
    "greeting_tool",
]


class GreetingType(str, enum.Enum):
    """Type of greeting to deliver."""

    NEW_USER = "new_user"
    RETURNING_USER = "returning_user"
    NEW_TENANT = "new_tenant"


class UserContext(BaseModel):
    """Known context about the user starting a conversation."""

    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    language: str = "en"
    is_new_user: bool = True


class GreetingResponse(BaseModel):
    """A generated greeting."""

    message: str
    greeting_type: GreetingType
    quick_replies: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


_GREETINGS: dict[str, dict[GreetingType, str]] = {
    "en": {
        GreetingType.NEW_USER: (
            "Hi{name}! Welcome to Stratum AI - your revenue operating system for "
            "Facebook, Instagram and WhatsApp campaigns. I'll help you get set up "
            "in just a few minutes. Ready to start?"
        ),
        GreetingType.RETURNING_USER: (
            "Welcome back{name}! Let's pick up where you left off with your "
            "Stratum AI setup."
        ),
        GreetingType.NEW_TENANT: (
            "Hi{name}! Let's set up your new workspace. I'll walk you through "
            "connecting your ad accounts and configuring Trust-Gated Autopilot."
        ),
    },
    "ar": {
        GreetingType.NEW_USER: (
            "مرحبا{name}! أهلاً بك في Stratum AI. سأساعدك في إعداد حسابك خلال دقائق. "
            "هل أنت مستعد للبدء؟"
        ),
        GreetingType.RETURNING_USER: "أهلاً بعودتك{name}! لنكمل الإعداد من حيث توقفنا.",
        GreetingType.NEW_TENANT: (
            "مرحبا{name}! لنقم بإعداد مساحة العمل الجديدة الخاصة بك خطوة بخطوة."
        ),
    },
}

_DEFAULT_QUICK_REPLIES = ["Get Started", "Learn More", "Watch Demo", "Talk to Sales"]


class GreetingTool:
    """Generates localized greetings based on user context."""

    def greet(self, context: UserContext) -> GreetingResponse:
        """Build a greeting appropriate for the user's context."""
        if context.is_new_user:
            greeting_type = GreetingType.NEW_USER
        elif context.tenant_id and not context.user_id:
            greeting_type = GreetingType.NEW_TENANT
        else:
            greeting_type = GreetingType.RETURNING_USER

        templates = _GREETINGS.get(context.language, _GREETINGS["en"])
        name_part = f" {context.name}" if context.name else ""
        message = templates[greeting_type].replace("{name}", name_part)

        return GreetingResponse(
            message=message,
            greeting_type=greeting_type,
            quick_replies=list(_DEFAULT_QUICK_REPLIES),
        )


# Shared singleton instance
greeting_tool = GreetingTool()


def greet_user(context: UserContext) -> GreetingResponse:
    """Convenience wrapper around the shared GreetingTool instance."""
    return greeting_tool.greet(context)
