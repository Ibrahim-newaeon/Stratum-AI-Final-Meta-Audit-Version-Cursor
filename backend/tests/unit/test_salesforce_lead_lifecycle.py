# =============================================================================
# Stratum AI - Salesforce Lead Lifecycle Stage Tests
# =============================================================================
"""
Unit tests for the lifecycle stage ``SalesforceSyncService`` gives a lead.

``LifecycleStage`` has four rungs - anonymous, known, customer, churned - and
the lead path reached for a fifth that does not exist (``ENGAGED``), so every
Salesforce lead whose Status contained "qualified" raised ``AttributeError``
mid-sync. A qualified lead is identified but has not purchased, which is KNOWN.

What is pinned here:

* a qualified lead is KNOWN rather than raising,
* a converted lead is still CUSTOMER,
* an identified lead with an ordinary status is still KNOWN,
* a lead with neither a qualifying status nor an email keeps the stage it had.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

import app.models  # noqa: F401  (registers every mapper so ORM statements compile)
from app.models.cdp import CDPProfile, LifecycleStage
from app.services.crm.salesforce_sync import SalesforceSyncService

pytestmark = pytest.mark.unit

TENANT = 1


class _FakeIdentityService:
    """Returns one profile, the way identity resolution would."""

    def __init__(self, profile: CDPProfile) -> None:
        self.profile = profile

    async def resolve_profile(self, identifiers: list[dict[str, Any]]) -> CDPProfile:
        return self.profile


async def process_lead_stage(
    status: str | None, *, email: str | None = "lead@example.com"
) -> Any:
    """Run ``_process_lead`` for one lead and return the stage it assigned."""
    now = datetime.now(UTC)
    profile = CDPProfile(
        tenant_id=TENANT,
        created_at=now,
        updated_at=now,
        profile_data={},
    )

    service = SalesforceSyncService(db=None, tenant_id=TENANT)  # type: ignore[arg-type]
    service.identity_service = _FakeIdentityService(profile)  # type: ignore[assignment]

    lead = {"Id": "00Q000000000001", "Email": email, "Status": status}
    await service._process_lead(lead)

    return profile.lifecycle_stage


async def test_a_qualified_lead_is_known():
    """The branch that used to raise: Salesforce's own qualified statuses."""
    for status in ("Qualified", "Marketing Qualified Lead", "SALES QUALIFIED"):
        assert await process_lead_stage(status) == LifecycleStage.KNOWN.value


async def test_a_converted_lead_is_still_a_customer():
    """Conversion outranks qualification and is unchanged."""
    assert await process_lead_stage("Closed - Converted") == LifecycleStage.CUSTOMER.value


async def test_an_identified_lead_is_still_known():
    """An ordinary status with an email is unchanged."""
    assert await process_lead_stage("Open - Not Contacted") == LifecycleStage.KNOWN.value


async def test_an_unidentified_lead_keeps_its_stage():
    """No qualifying status and no email leaves the profile's stage alone."""
    assert await process_lead_stage("Open - Not Contacted", email=None) is None
    assert await process_lead_stage(None, email=None) is None
