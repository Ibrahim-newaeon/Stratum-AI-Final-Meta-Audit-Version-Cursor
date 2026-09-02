# =============================================================================
# Stratum AI - GTM Service Unit Tests (tag deployment only)
# =============================================================================
"""
Unit tests for ``app.services.measurement.gtm_service``.

No network, no database: HTTP goes through ``httpx.MockTransport`` and the
session is an ``AsyncMock``.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.models.cdp import CDPSource, SourceType
from app.services.measurement import gtm_service
from app.services.measurement.gtm_service import (
    GTM_CONTAINER_ID_RE,
    GTMSnippets,
    GTMVerifyResult,
    build_snippets,
    ensure_sgtm_source,
    validate_container_id,
    validate_server_container_url,
    verify_containers,
)

pytestmark = pytest.mark.unit

INGEST_URL = "https://app.stratum.ai/api/v1/cdp/ingest"
SOURCE_KEY = "cdp_test_source_key_123"


# =============================================================================
# Validation
# =============================================================================


def test_container_id_regex_shape():
    assert GTM_CONTAINER_ID_RE.pattern == r"^GTM-[A-Z0-9]{4,10}$"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("GTM-ABC123", "GTM-ABC123"),
        ("  gtm-abc123 ", "GTM-ABC123"),
        ("GTM-A1B2C3D4E5", "GTM-A1B2C3D4E5"),
        ("GTM-WXYZ", "GTM-WXYZ"),
    ],
)
def test_validate_container_id_ok(raw, expected):
    assert validate_container_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "GTM-", "GTM-ABC", "ABC123", "G-ABC1234", "GTM-ABCDEFGHIJK", "GTM-AB C12", None],
)
def test_validate_container_id_rejects(raw):
    with pytest.raises(ValueError) as exc_info:
        validate_container_id(raw)
    assert "Invalid GTM container id, expected GTM-XXXXXXX" in str(exc_info.value)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://sgtm.example.com", "https://sgtm.example.com"),
        ("https://sgtm.example.com/", "https://sgtm.example.com"),
        ("  https://sgtm.example.com/tagging/  ", "https://sgtm.example.com/tagging"),
        ("https://sgtm.example.com:8443", "https://sgtm.example.com:8443"),
    ],
)
def test_validate_server_container_url_ok(raw, expected):
    assert validate_server_container_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "http://sgtm.example.com",
        "sgtm.example.com",
        "https://",
        "https://sgtm.example.com/?x=1",
        "https://sgtm.example.com/#frag",
        "https://user:pw@sgtm.example.com",
        "ftp://sgtm.example.com",
    ],
)
def test_validate_server_container_url_rejects(raw):
    with pytest.raises(ValueError):
        validate_server_container_url(raw)


# =============================================================================
# Snippets
# =============================================================================


def test_build_snippets_web_container_only():
    snippets = build_snippets("GTM-ABC123", None, INGEST_URL, SOURCE_KEY, "1234567890")
    assert isinstance(snippets, GTMSnippets)

    assert "GTM-ABC123" in snippets.head_snippet
    assert "https://www.googletagmanager.com/gtm.js?id=" in snippets.head_snippet
    assert "GTM-ABC123" in snippets.body_snippet
    assert "https://www.googletagmanager.com/ns.html?id=GTM-ABC123" in snippets.body_snippet

    # Stratum snippet posts {events:[...]} to the ingest URL with X-Source-Key
    assert INGEST_URL in snippets.stratum_snippet
    assert SOURCE_KEY in snippets.stratum_snippet
    assert "X-Source-Key" in snippets.stratum_snippet
    assert "events:[ev]" in snippets.stratum_snippet
    assert "dataLayer" in snippets.stratum_snippet

    assert snippets.sgtm_config["transport_url"] is None
    assert snippets.sgtm_config["meta_pixel_id"] == "1234567890"


def test_build_snippets_server_container_loads_gtm_js_first_party():
    snippets = build_snippets(
        "gtm-abc123", "https://sgtm.example.com", INGEST_URL, SOURCE_KEY, None
    )
    assert "'https://sgtm.example.com/gtm.js?id='" in snippets.head_snippet
    assert "www.googletagmanager.com" not in snippets.head_snippet
    assert "https://sgtm.example.com/ns.html?id=GTM-ABC123" in snippets.body_snippet
    assert snippets.sgtm_config["server_container_url"] == "https://sgtm.example.com"
    assert snippets.sgtm_config["transport_url"] == "https://sgtm.example.com"


def test_build_snippets_sgtm_config_keys_match_contract():
    snippets = build_snippets(
        "GTM-ABC123", "https://sgtm.example.com", INGEST_URL, SOURCE_KEY, "42"
    )
    assert set(snippets.sgtm_config.keys()) == {
        "server_container_url",
        "transport_url",
        "stratum_ingest_url",
        "source_key",
        "source_header",
        "cdp_source_type",
        "meta_capi_client",
        "meta_pixel_id",
        "role",
    }
    assert snippets.sgtm_config["stratum_ingest_url"] == INGEST_URL
    assert snippets.sgtm_config["source_key"] == SOURCE_KEY
    assert snippets.sgtm_config["source_header"] == "X-Source-Key"
    assert snippets.sgtm_config["cdp_source_type"] == "sgtm"
    assert snippets.sgtm_config["meta_capi_client"] == "Meta Conversions API tag (server container)"
    assert snippets.sgtm_config["role"] == "tag_deployment"


def test_build_snippets_without_container_returns_placeholder():
    snippets = build_snippets(None, None, INGEST_URL, None, None)
    assert snippets.head_snippet.startswith("<!--")
    assert snippets.body_snippet.startswith("<!--")
    assert "GTM-" in snippets.head_snippet  # hint for the operator
    # Without a source key the Stratum snippet is inert but still well-formed
    assert 'var SOURCE_KEY=""' in snippets.stratum_snippet


def test_stratum_snippet_escapes_javascript_strings():
    snippets = build_snippets("GTM-ABC123", None, INGEST_URL, 'cdp_"quoted"</script>', None)
    assert "</script>'" not in snippets.stratum_snippet.split("var SOURCE_KEY=")[1].split("\n")[0]
    assert '\\"quoted\\"' in snippets.stratum_snippet


# =============================================================================
# ensure_sgtm_source
# =============================================================================


def _db_returning(source):
    """Build an AsyncMock session whose select returns ``source``."""
    result = MagicMock()
    result.scalars.return_value.first.return_value = source
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


async def test_ensure_sgtm_source_creates_when_missing():
    db = _db_returning(None)
    source = await ensure_sgtm_source(db, 7, "GTM-ABC123", "https://sgtm.example.com")

    assert isinstance(source, CDPSource)
    db.add.assert_called_once_with(source)
    db.flush.assert_awaited()
    assert source.tenant_id == 7
    assert source.name == "Server-side GTM"
    assert source.source_type == SourceType.SGTM.value == "sgtm"
    assert source.source_key.startswith("cdp_")
    assert len(source.source_key) > 20
    assert source.config == {
        "web_container_id": "GTM-ABC123",
        "server_container_url": "https://sgtm.example.com",
        "role": "tag_deployment",
    }
    assert source.is_active is True


async def test_ensure_sgtm_source_updates_existing_without_rotating_key():
    existing = CDPSource(
        tenant_id=7,
        name="Server-side GTM",
        source_type="sgtm",
        source_key="cdp_existing_key",
        config={"web_container_id": "GTM-OLD111", "custom": "keep"},
        is_active=False,
    )
    db = _db_returning(existing)
    source = await ensure_sgtm_source(db, 7, "GTM-NEW222", None)

    assert source is existing
    db.add.assert_not_called()
    assert source.source_key == "cdp_existing_key"
    assert source.config["web_container_id"] == "GTM-NEW222"
    assert source.config["server_container_url"] is None
    assert source.config["role"] == "tag_deployment"
    assert source.config["custom"] == "keep"
    assert source.is_active is True


# =============================================================================
# verify_containers
# =============================================================================


def _mock_client_factory(handler):
    def factory(timeout_seconds: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout_seconds)

    return factory


async def test_verify_containers_both_ok(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(gtm_service, "_make_http_client", _mock_client_factory(handler))
    result = await verify_containers("gtm-abc123", "https://sgtm.example.com/", timeout_seconds=2)

    assert isinstance(result, GTMVerifyResult)
    assert result.success is True
    assert result.web_container_ok is True
    assert result.server_container_ok is True
    assert result.checked_at.tzinfo is not None
    assert result.checked_at <= datetime.now(UTC)
    assert "https://www.googletagmanager.com/gtm.js?id=GTM-ABC123" in seen
    assert "https://sgtm.example.com/healthy" in seen


async def test_verify_containers_web_missing_server_failing(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "googletagmanager" in str(request.url):
            return httpx.Response(404)
        raise httpx.ConnectError("boom", request=request)

    monkeypatch.setattr(gtm_service, "_make_http_client", _mock_client_factory(handler))
    result = await verify_containers("GTM-ABC123", "https://sgtm.example.com")

    assert result.success is False
    assert result.web_container_ok is False
    assert result.server_container_ok is False
    assert "HTTP 404" in result.message
    assert "ConnectError" in result.message


async def test_verify_containers_only_web(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    monkeypatch.setattr(gtm_service, "_make_http_client", _mock_client_factory(handler))
    result = await verify_containers("GTM-ABC123", None)
    assert result.success is True
    assert result.web_container_ok is True
    assert result.server_container_ok is None


async def test_verify_containers_nothing_configured_never_raises():
    result = await verify_containers(None, None)
    assert result.success is False
    assert result.web_container_ok is None
    assert result.server_container_ok is None
    assert "No GTM containers configured" in result.message


async def test_verify_containers_swallows_client_factory_errors(monkeypatch):
    def broken_factory(timeout_seconds: float):
        raise RuntimeError("no http client")

    monkeypatch.setattr(gtm_service, "_make_http_client", broken_factory)
    result = await verify_containers("GTM-ABC123", "https://sgtm.example.com")
    assert result.success is False
    assert result.web_container_ok is False
    assert result.server_container_ok is False
    assert "RuntimeError" in result.message
