# =============================================================================
# Stratum AI - Measurement & Verification Schema Tests
# =============================================================================
"""
Unit tests for ``app.schemas.measurement``.

Covers:
- GA4 property ID / measurement ID validators
- GTM container ID / https-only server container URL validators
- Conversion event name normalization
- Response models never exposing secrets (service_account_json, preview_header)
"""

import json

import pytest
from pydantic import ValidationError

from app.schemas.measurement import (
    GA4_READONLY_SCOPE,
    GA4ConfigRequest,
    GA4ConfigResponse,
    GA4StatusResponse,
    GA4SyncRequest,
    GA4TestRequest,
    GTMConfigRequest,
    GTMConfigResponse,
    GTMStatusResponse,
    MeasurementStatusResponse,
    validate_gtm_container_id,
    validate_https_url,
    validate_measurement_id,
    validate_property_id,
)

pytestmark = pytest.mark.unit

FAKE_SERVICE_ACCOUNT = json.dumps(
    {
        "type": "service_account",
        "project_id": "stratum-test",
        "client_email": "ga4-reader@stratum-test.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n",
    }
)


# =============================================================================
# GA4 validators
# =============================================================================


class TestPropertyIdValidation:
    """GA4 property_id must be numeric (^[0-9]{1,64}$)."""

    @pytest.mark.parametrize("value", ["1", "123456789", " 987654321 ", "0" * 64])
    def test_accepts_numeric_property_ids(self, value: str) -> None:
        assert validate_property_id(value) == value.strip()

    @pytest.mark.parametrize(
        "value", ["", "abc", "G-ABC123", "properties/123456", "12 34", "1" * 65, "-1"]
    )
    def test_rejects_non_numeric_property_ids(self, value: str) -> None:
        with pytest.raises(ValueError):
            validate_property_id(value)

    def test_request_model_rejects_bad_property_id(self) -> None:
        with pytest.raises(ValidationError):
            GA4ConfigRequest(property_id="not-a-property", service_account_json=FAKE_SERVICE_ACCOUNT)

    def test_request_model_accepts_valid_config(self) -> None:
        req = GA4ConfigRequest(
            property_id="123456789",
            measurement_id="g-abcd1234",
            service_account_json=FAKE_SERVICE_ACCOUNT,
        )
        assert req.property_id == "123456789"
        assert req.measurement_id == "G-ABCD1234"
        assert req.conversion_event_names == ["purchase"]
        assert req.is_active is True


class TestMeasurementIdValidation:
    """GA4 measurement_id must match ^G-[A-Z0-9]{4,12}$ (upper-cased)."""

    @pytest.mark.parametrize("value", ["G-ABCD", "G-1234567890AB", "g-abc123", " G-XYZ789 "])
    def test_accepts_measurement_ids(self, value: str) -> None:
        result = validate_measurement_id(value)
        assert result.startswith("G-")
        assert result == result.upper()

    @pytest.mark.parametrize("value", ["", "G-ABC", "GA-ABCD1234", "G-1234567890ABC", "UA-12345-1", "G_ABCD"])
    def test_rejects_invalid_measurement_ids(self, value: str) -> None:
        with pytest.raises(ValueError):
            validate_measurement_id(value)

    def test_blank_measurement_id_becomes_none(self) -> None:
        req = GA4ConfigRequest(
            property_id="1", measurement_id="   ", service_account_json=FAKE_SERVICE_ACCOUNT
        )
        assert req.measurement_id is None


class TestServiceAccountJsonValidation:
    """service_account_json is optional but must be a structurally valid key file when set."""

    def test_omitted_json_is_allowed(self) -> None:
        req = GA4ConfigRequest(property_id="123")
        assert req.service_account_json is None

    @pytest.mark.parametrize("value", ["not json", "[]", "{}", '{"client_email": "x"}'])
    def test_rejects_invalid_json(self, value: str) -> None:
        with pytest.raises(ValidationError):
            GA4ConfigRequest(property_id="123", service_account_json=value)

    def test_test_request_validates_the_same_way(self) -> None:
        with pytest.raises(ValidationError):
            GA4TestRequest(service_account_json="{}")
        ok = GA4TestRequest(property_id="42", service_account_json=FAKE_SERVICE_ACCOUNT)
        assert ok.property_id == "42"


class TestConversionEventNames:
    """Conversion event names: non-empty strings, de-duplicated, at most 20."""

    def test_normalizes_and_dedupes(self) -> None:
        req = GA4ConfigRequest(
            property_id="1",
            conversion_event_names=[" purchase ", "purchase", "generate_lead"],
        )
        assert req.conversion_event_names == ["purchase", "generate_lead"]

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError):
            GA4ConfigRequest(property_id="1", conversion_event_names=["purchase", ""])

    def test_rejects_empty_list(self) -> None:
        with pytest.raises(ValidationError):
            GA4ConfigRequest(property_id="1", conversion_event_names=[])

    def test_rejects_more_than_twenty(self) -> None:
        with pytest.raises(ValidationError):
            GA4ConfigRequest(
                property_id="1",
                conversion_event_names=[f"event_{i}" for i in range(21)],
            )

    def test_accepts_exactly_twenty(self) -> None:
        req = GA4ConfigRequest(
            property_id="1", conversion_event_names=[f"event_{i}" for i in range(20)]
        )
        assert len(req.conversion_event_names) == 20


class TestGA4SyncRequest:
    """lookback_days is bounded to 1-90."""

    @pytest.mark.parametrize("days", [0, 91, -5])
    def test_rejects_out_of_range_lookback(self, days: int) -> None:
        with pytest.raises(ValidationError):
            GA4SyncRequest(lookback_days=days)

    def test_defaults(self) -> None:
        req = GA4SyncRequest()
        assert req.lookback_days is None
        assert req.backfill is False


# =============================================================================
# GTM validators
# =============================================================================


class TestGTMContainerIdValidation:
    """GTM container IDs must match ^GTM-[A-Z0-9]{4,10}$ (upper-cased)."""

    @pytest.mark.parametrize("value", ["GTM-ABCD", "GTM-ABCD1234", "gtm-abc1234", " GTM-XYZ12345 "])
    def test_accepts_container_ids(self, value: str) -> None:
        result = validate_gtm_container_id(value)
        assert result.startswith("GTM-")
        assert result == result.upper()

    @pytest.mark.parametrize("value", ["", "GTM-ABC", "GTM-ABCDEFGHIJK", "G-ABCD1234", "GTMABCD1234", "GTM_ABCD"])
    def test_rejects_invalid_container_ids(self, value: str) -> None:
        with pytest.raises(ValueError):
            validate_gtm_container_id(value)

    def test_request_model_normalizes_ids(self) -> None:
        req = GTMConfigRequest(web_container_id="gtm-abcd123", server_container_id="gtm-srv1234")
        assert req.web_container_id == "GTM-ABCD123"
        assert req.server_container_id == "GTM-SRV1234"

    def test_request_model_rejects_bad_ids(self) -> None:
        with pytest.raises(ValidationError):
            GTMConfigRequest(web_container_id="UA-1234")


class TestServerContainerUrlValidation:
    """Server-side tagging endpoint must be https and has no trailing slash."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("https://sgtm.example.com", "https://sgtm.example.com"),
            ("https://sgtm.example.com/", "https://sgtm.example.com"),
            ("https://tags.example.com/collect/", "https://tags.example.com/collect"),
            ("  https://sgtm.example.com  ", "https://sgtm.example.com"),
        ],
    )
    def test_accepts_https_urls(self, value: str, expected: str) -> None:
        assert validate_https_url(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "http://sgtm.example.com",
            "ftp://sgtm.example.com",
            "sgtm.example.com",
            "https://",
            "https://sgtm.example.com/?x=1",
            "javascript:alert(1)",
        ],
    )
    def test_rejects_non_https_urls(self, value: str) -> None:
        with pytest.raises(ValueError):
            validate_https_url(value)

    def test_request_model_rejects_http_url(self) -> None:
        with pytest.raises(ValidationError):
            GTMConfigRequest(server_container_url="http://sgtm.example.com")

    def test_request_model_accepts_https_url(self) -> None:
        req = GTMConfigRequest(server_container_url="https://sgtm.example.com/")
        assert req.server_container_url == "https://sgtm.example.com"
        assert req.deploy_meta_pixel is True
        assert req.deploy_meta_capi is True
        assert req.deploy_stratum_snippet is True


class TestGTMConfigRequestRules:
    """A GTM config must reference at least one container."""

    def test_requires_web_or_server_container(self) -> None:
        with pytest.raises(ValidationError):
            GTMConfigRequest()

    def test_blank_preview_header_is_kept_as_clear_signal(self) -> None:
        req = GTMConfigRequest(web_container_id="GTM-ABCD123", preview_header="")
        assert req.preview_header == ""

    def test_omitted_preview_header_is_none(self) -> None:
        req = GTMConfigRequest(web_container_id="GTM-ABCD123")
        assert req.preview_header is None

    def test_meta_pixel_id_must_be_numeric(self) -> None:
        with pytest.raises(ValidationError):
            GTMConfigRequest(web_container_id="GTM-ABCD123", meta_pixel_id="pixel-1")
        req = GTMConfigRequest(web_container_id="GTM-ABCD123", meta_pixel_id="123456789012345")
        assert req.meta_pixel_id == "123456789012345"


# =============================================================================
# Responses never leak secrets
# =============================================================================


class TestResponsesNeverExposeSecrets:
    """Response models must not have (or accept) secret-bearing fields."""

    def test_ga4_config_response_has_no_service_account_json_field(self) -> None:
        assert "service_account_json" not in GA4ConfigResponse.model_fields
        assert "service_account_json_encrypted" not in GA4ConfigResponse.model_fields

    def test_ga4_config_response_ignores_injected_secret(self) -> None:
        resp = GA4ConfigResponse(
            property_id="123",
            has_credentials=True,
            service_account_json=FAKE_SERVICE_ACCOUNT,  # type: ignore[call-arg]
        )
        dumped = resp.model_dump()
        assert "service_account_json" not in dumped
        assert FAKE_SERVICE_ACCOUNT not in json.dumps(dumped)
        assert dumped["configured"] is True
        assert dumped["access"] == "read_only"
        assert dumped["scope"] == GA4_READONLY_SCOPE
        assert dumped["scope"] == "https://www.googleapis.com/auth/analytics.readonly"

    def test_gtm_config_response_has_no_preview_header_field(self) -> None:
        assert "preview_header" not in GTMConfigResponse.model_fields
        assert "preview_header_encrypted" not in GTMConfigResponse.model_fields
        resp = GTMConfigResponse(
            web_container_id="GTM-ABCD123",
            has_preview_header=True,
            preview_header="secret-header",  # type: ignore[call-arg]
        )
        dumped = resp.model_dump()
        assert "preview_header" not in dumped
        assert dumped["role"] == "tag_deployment"
        assert dumped["configured"] is True

    def test_status_responses_default_to_unconfigured(self) -> None:
        status = MeasurementStatusResponse()
        assert status.ga4.configured is False
        assert status.ga4.status == "disconnected"
        assert status.ga4.access == "read_only"
        assert status.gtm.configured is False
        assert status.gtm.role == "tag_deployment"
        assert isinstance(status.ga4, GA4StatusResponse)
        assert isinstance(status.gtm, GTMStatusResponse)

    def test_status_literal_is_enforced(self) -> None:
        with pytest.raises(ValidationError):
            GA4StatusResponse(configured=True, status="paused")
