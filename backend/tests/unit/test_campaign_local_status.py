"""Local campaign pause/activate routes must not touch Meta write clients."""

from __future__ import annotations

import inspect

import pytest

from app.api.v1.endpoints import campaigns as campaigns_module
from app.models import CampaignStatus

pytestmark = pytest.mark.unit


def test_pause_and_activate_routes_are_registered() -> None:
    paths = {getattr(route, "path", None) for route in campaigns_module.router.routes}
    assert "/{campaign_id}/pause" in paths
    assert "/{campaign_id}/activate" in paths


def test_local_status_helper_docs_forbid_meta_writes() -> None:
    source = inspect.getsource(campaigns_module._set_local_campaign_status)
    assert "do **not** call Meta write clients" in source or "do not call Meta write" in source.lower()
    assert CampaignStatus.PAUSED.value == "paused"
    assert CampaignStatus.ACTIVE.value == "active"


def test_pause_activate_docstrings_say_local_only() -> None:
    pause_doc = inspect.getdoc(campaigns_module.pause_campaign) or ""
    activate_doc = inspect.getdoc(campaigns_module.activate_campaign) or ""
    assert "local" in pause_doc.lower()
    assert "meta" in pause_doc.lower()
    assert "local" in activate_doc.lower()
    assert "meta" in activate_doc.lower()
