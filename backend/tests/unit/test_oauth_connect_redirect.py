"""Connect Platforms is a dashboard SPA route, not /connect."""

import inspect
from urllib.parse import parse_qs, urlparse

import pytest

from app.api.v1.endpoints import dashboard as dashboard_module
from app.api.v1.endpoints.oauth import FRONTEND_CONNECT_PATH, frontend_connect_url
from app.core.config import settings

pytestmark = pytest.mark.unit


def test_dashboard_quick_action_uses_the_dashboard_connect_path() -> None:
    source = inspect.getsource(dashboard_module)
    assert "action=FRONTEND_CONNECT_PATH" in source
    assert 'action="/connect"' not in source


def test_frontend_connect_url_is_dashboard_campaigns_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test")
    url = frontend_connect_url(platform="meta", status="success")
    parsed = urlparse(url)
    assert parsed.path == FRONTEND_CONNECT_PATH
    assert parsed.path == "/dashboard/campaigns/connect"
    assert parse_qs(parsed.query) == {"platform": ["meta"], "status": ["success"]}


def test_frontend_connect_url_encodes_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "frontend_url", "http://localhost:5173/")
    url = frontend_connect_url(
        platform="meta",
        error="invalid_state",
        message="Session expired, please try again",
    )
    parsed = urlparse(url)
    assert parsed.path == "/dashboard/campaigns/connect"
    assert parse_qs(parsed.query)["message"] == ["Session expired, please try again"]
