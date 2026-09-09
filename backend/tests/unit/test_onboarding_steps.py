# =============================================================================
# Stratum AI - Onboarding step persistence tests
# =============================================================================
"""
Unit tests for ``complete_onboarding_step``'s field handling.

Three separate faults met here in production: the SPA posted to
``/onboarding/steps/<step>``, which does not exist; it sent the step payload as
the whole body rather than ``{step, data}``; and three of the keys it did send
never matched a column, so even a correctly addressed request dropped them.

The endpoint used to walk ``payload.data`` and ``setattr`` anything the model
happened to have, excluding only ``id`` and ``tenant_id``. That silently
discarded the three renamed keys *and* let a caller write ``status`` or
``completed_steps`` - skipping the wizard by posting a step. Both behaviours are
asserted against here.

No database: the record is a plain object with the columns the endpoint touches.
"""

from typing import Any

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.onboarding import _MAJOR_UNIT_FIELDS, _STEP_FIELDS, _to_cents
from app.models.onboarding import OnboardingStep

pytestmark = pytest.mark.unit


def apply_step(step: OnboardingStep, data: dict[str, Any]) -> dict[str, Any]:
    """
    Run the endpoint's field-mapping rules and return what would be written.

    Mirrors the loop in ``complete_onboarding_step`` so the mapping can be
    asserted without a database session or an HTTP round trip.
    """
    allowed = _STEP_FIELDS.get(step, {})
    written: dict[str, Any] = {}
    for field_name, value in data.items():
        column = allowed.get(field_name)
        if column is None or value is None:
            continue
        if field_name in _MAJOR_UNIT_FIELDS:
            value = _to_cents(value, field_name)
        written[column] = value
    return written


class TestRenamedFields:
    """The three keys that silently vanished before."""

    def test_platforms_lands_in_selected_platforms(self):
        written = apply_step(
            OnboardingStep.PLATFORM_SELECTION, {"platforms": ["meta", "instagram"]}
        )
        assert written == {"selected_platforms": ["meta", "instagram"]}

    def test_monthly_budget_lands_in_the_cents_column(self):
        written = apply_step(OnboardingStep.GOALS_SETUP, {"monthly_budget": 5000})
        assert written == {"monthly_budget_cents": 500_000}

    def test_target_cpa_lands_in_the_cents_column(self):
        written = apply_step(OnboardingStep.GOALS_SETUP, {"target_cpa": 12.5})
        assert written == {"target_cpa_cents": 1250}


class TestMoneyConversion:
    """
    ``*_cents`` holds hundredths of the account's **major** unit.

    The wizard's inputs are plain numbers - "5000" means 5000 of the tenant's
    currency - so the endpoint scales rather than trusting the browser.
    """

    def test_whole_units_scale_by_a_hundred(self):
        assert _to_cents(5000, "monthly_budget") == 500_000

    def test_fractional_units_round_to_the_nearest_hundredth(self):
        assert _to_cents(12.345, "target_cpa") == 1234

    def test_zero_is_allowed(self):
        assert _to_cents(0, "monthly_budget") == 0

    @pytest.mark.parametrize(
        "value",
        ["5000", None, [], {}, True, float("nan"), float("inf"), -1],
    )
    def test_a_value_that_is_not_a_usable_amount_is_refused(self, value):
        """Storing a budget nobody meant is worse than refusing the step."""
        with pytest.raises(HTTPException) as exc:
            _to_cents(value, "monthly_budget")
        assert exc.value.status_code == 422


class TestAllowlist:
    """A step may write its own fields and nothing else."""

    def test_protected_columns_cannot_be_written(self):
        """The mass-assignment hole: posting `status` used to skip the wizard."""
        written = apply_step(
            OnboardingStep.BUSINESS_PROFILE,
            {
                "industry": "ecommerce",
                "status": "completed",
                "completed_steps": ["business_profile", "platform_selection"],
                "current_step": "completed",
                "tenant_id": 999,
                "id": 1,
            },
        )
        assert written == {"industry": "ecommerce"}

    def test_a_field_from_another_step_is_ignored(self):
        written = apply_step(
            OnboardingStep.PLATFORM_SELECTION,
            {"platforms": ["meta"], "monthly_budget": 5000},
        )
        assert written == {"selected_platforms": ["meta"]}

    def test_none_is_skipped_rather_than_nulling_a_column(self):
        """The wizard sends undefined for optional fields it did not collect."""
        written = apply_step(
            OnboardingStep.GOALS_SETUP,
            {"primary_kpi": "roas", "target_roas": None, "target_cpa": None},
        )
        assert written == {"primary_kpi": "roas"}

    def test_every_step_has_a_field_map(self):
        """A new step without one would accept the form and store nothing."""
        assert set(_STEP_FIELDS) == set(OnboardingStep)


class TestPayloadKeysMatchTheClient:
    """
    The keys asserted here are the ones frontend/src/api/onboarding.ts sends.

    They are spelled out so that renaming a payload field on either side breaks
    a test rather than silently dropping data again.
    """

    def test_business_profile(self):
        assert set(_STEP_FIELDS[OnboardingStep.BUSINESS_PROFILE]) == {
            "industry",
            "industry_other",
            "monthly_ad_spend",
            "team_size",
            "company_website",
            "target_markets",
        }

    def test_goals_setup(self):
        assert set(_STEP_FIELDS[OnboardingStep.GOALS_SETUP]) == {
            "primary_kpi",
            "target_roas",
            "target_cpa",
            "monthly_budget",
            "currency",
            "timezone",
        }

    def test_automation_preferences(self):
        assert set(_STEP_FIELDS[OnboardingStep.AUTOMATION_PREFERENCES]) == {
            "automation_mode",
            "auto_pause_enabled",
            "auto_scale_enabled",
            "notification_email",
            "notification_slack",
            "notification_whatsapp",
        }

    def test_trust_gate_config(self):
        assert set(_STEP_FIELDS[OnboardingStep.TRUST_GATE_CONFIG]) == {
            "trust_threshold_autopilot",
            "trust_threshold_alert",
            "require_approval_above",
            "max_daily_actions",
        }
