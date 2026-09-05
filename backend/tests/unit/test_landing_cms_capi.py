# =============================================================================
# Stratum AI - Landing Page CAPI Lead Send Tests
# =============================================================================
"""
Unit tests for the landing page CAPI lead send in
``app.api.v1.endpoints.landing_cms``.

No network and no database: the connector and the session factory are fakes
that record what they were actually asked to do.

What is pinned here:

* the connector is driven through its real contract - ``MetaCAPIConnector()``
  then ``connect(credentials)`` then ``send_events([event])`` - and the
  constructor shape that used to be called here raises TypeError,
* ``capi_sent`` is set only when Meta accepted the event; an unconfigured
  pixel, a failed connection and a rejected event all leave it false and
  record why in ``capi_results``,
* a successful send is never un-done by a later failure,
* the lead maps to a Meta ``Lead`` event with a deterministic event id, a
  well-formed ``fbc``, and no null identifiers,
* the module no longer uses the FastAPI session dependency as a context
  manager, which raised ``AttributeError: __aenter__`` on every request.
"""

import hashlib
import inspect
import json
from datetime import UTC, datetime
from typing import Any, ClassVar, Self

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.models  # noqa: F401  (registers every mapper so the ORM model builds)
from app.api.v1.endpoints import landing_cms
from app.base_models import LandingPageSubscriber
from app.services.capi import platform_connectors
from app.services.capi.platform_connectors import (
    CAPIResponse,
    ConnectionResult,
    ConnectionStatus,
    MetaCAPIConnector,
)

pytestmark = pytest.mark.unit


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# =============================================================================
# Fakes
# =============================================================================
class FakeResult:
    """Stands in for the SQLAlchemy Result of a single-row select."""

    def __init__(self, subscriber: LandingPageSubscriber | None):
        self._subscriber = subscriber

    def scalar_one_or_none(self) -> LandingPageSubscriber | None:
        return self._subscriber


class FakeSession:
    """Async session that returns one subscriber and counts commits."""

    def __init__(self, subscriber: LandingPageSubscriber | None):
        self.subscriber = subscriber
        self.commits = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def execute(self, _statement: Any) -> FakeResult:
        return FakeResult(self.subscriber)

    async def commit(self) -> None:
        self.commits += 1


class FakeSessionFactory:
    """``AsyncSessionLocal`` replacement that hands out one shared session."""

    def __init__(self, subscriber: LandingPageSubscriber | None):
        self.session = FakeSession(subscriber)
        self.calls = 0

    def __call__(self) -> FakeSession:
        self.calls += 1
        return self.session


class FakeConnector:
    """Records the real connector calls instead of talking to Meta."""

    connect_result: ConnectionResult
    send_result: CAPIResponse
    instances: ClassVar[list["FakeConnector"]] = []

    def __init__(self) -> None:
        self.credentials: dict[str, str] | None = None
        self.sent_batches: list[list[dict[str, Any]]] = []
        FakeConnector.instances.append(self)

    async def connect(self, credentials: dict[str, str]) -> ConnectionResult:
        self.credentials = credentials
        return self.connect_result

    async def send_events(self, events: list[dict[str, Any]]) -> CAPIResponse:
        self.sent_batches.append(events)
        return self.send_result


def connected() -> ConnectionResult:
    return ConnectionResult(
        status=ConnectionStatus.CONNECTED, platform="meta", message="ok"
    )


def refused(message: str = "Invalid access token") -> ConnectionResult:
    return ConnectionResult(
        status=ConnectionStatus.ERROR, platform="meta", message=message
    )


def accepted() -> CAPIResponse:
    return CAPIResponse(
        success=True,
        events_received=1,
        events_processed=1,
        errors=[],
        platform="meta",
        request_id="fbtrace-1",
    )


def rejected(message: str = "Invalid parameter") -> CAPIResponse:
    return CAPIResponse(
        success=False,
        events_received=1,
        events_processed=0,
        errors=[{"message": message}],
        platform="meta",
    )


# =============================================================================
# Fixtures
# =============================================================================
@pytest.fixture
def subscriber() -> LandingPageSubscriber:
    """A Meta-attributed lead with the identifiers a real signup carries."""
    row = LandingPageSubscriber(
        email="lead@example.com",
        full_name="Test Lead",
        phone="+15551234567",
        source_page="landing",
        language="en",
        utm_source="facebook",
        utm_campaign="q3-launch",
        landing_url="https://stratumai.app/en",
        fbclid="IwAR-click-id",
        fbp="fb.1.1700000000000.987654321",
        attributed_platform="meta",
        lead_score=70,
        ip_address="203.0.113.10",
        user_agent="Mozilla/5.0",
        capi_sent=False,
        created_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    )
    row.id = 42
    return row


@pytest.fixture
def wired(monkeypatch, subscriber):
    """Configured pixel, faked session factory and faked connector."""
    monkeypatch.setattr(
        landing_cms.settings, "meta_pixel_id", "111222333", raising=False
    )
    monkeypatch.setattr(
        landing_cms.settings, "meta_access_token", "house-token", raising=False
    )

    FakeConnector.instances = []
    FakeConnector.connect_result = connected()
    FakeConnector.send_result = accepted()
    monkeypatch.setattr(landing_cms, "MetaCAPIConnector", FakeConnector)

    factory = FakeSessionFactory(subscriber)
    monkeypatch.setattr(landing_cms, "AsyncSessionLocal", factory)
    return factory


def stored_results(subscriber: LandingPageSubscriber) -> dict[str, Any]:
    """Decode the JSON audit blob the task wrote."""
    assert subscriber.capi_results is not None
    return json.loads(subscriber.capi_results)


# =============================================================================
# The dead-code shape this path used to have
# =============================================================================
class TestConnectorContract:
    """Pin the real connector API so the old dead call cannot come back."""

    def test_constructor_rejects_positional_credentials(self):
        """``MetaCAPIConnector(pixel_id, access_token)`` was never valid."""
        with pytest.raises(TypeError):
            MetaCAPIConnector("111222333", "house-token")

    def test_connector_has_no_send_lead_event_method(self):
        """The method this path used to call does not exist on the connector."""
        assert not hasattr(MetaCAPIConnector, "send_lead_event")

    def test_credentials_are_supplied_through_connect(self):
        """Credentials reach the connector through ``connect``, not ``__init__``."""
        connector = MetaCAPIConnector()
        assert connector.pixel_id is None
        assert connector.access_token is None


class TestSessionUsage:
    """``get_async_session`` is an async generator, not a context manager."""

    def test_module_does_not_use_the_dependency_as_a_context_manager(self):
        source = inspect.getsource(landing_cms)
        assert "async with get_async_session()" not in source


# =============================================================================
# Event mapping
# =============================================================================
class TestLeadEventMapping:
    def test_uses_the_fbc_cookie_when_the_browser_sent_one(self):
        assert (
            landing_cms.build_fbc("fb.1.1699999999999.abc", "abc", datetime.now(UTC))
            == "fb.1.1699999999999.abc"
        )

    def test_rebuilds_a_well_formed_fbc_from_a_bare_fbclid(self):
        """A bare fbclid is not an fbc; it is rebuilt in Meta's documented format."""
        click_time = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        assert (
            landing_cms.build_fbc(None, "IwAR-click-id", click_time)
            == f"fb.1.{int(click_time.timestamp() * 1000)}.IwAR-click-id"
        )

    def test_returns_no_fbc_without_a_click_identifier(self):
        assert landing_cms.build_fbc(None, None, datetime.now(UTC)) is None

    def test_maps_a_subscriber_to_a_lead_event(self, subscriber):
        event = landing_cms.build_meta_lead_event(subscriber)

        assert event["event_name"] == "Lead"
        assert event["event_id"] == "landing-lead-42"
        assert event["event_time"] == int(subscriber.created_at.timestamp())
        assert event["action_source"] == "website"
        assert event["event_source_url"] == "https://stratumai.app/en"

        assert event["user_data"] == {
            "email": "lead@example.com",
            "phone": "+15551234567",
            "external_id": "42",
            "fbc": f"fb.1.{int(subscriber.created_at.timestamp() * 1000)}.IwAR-click-id",
            "fbp": "fb.1.1700000000000.987654321",
            "client_ip_address": "203.0.113.10",
            "client_user_agent": "Mozilla/5.0",
        }
        assert event["parameters"] == {
            "lead_type": "landing_page_signup",
            "utm_source": "facebook",
            "utm_campaign": "q3-launch",
            "lead_score": 70,
        }

    def test_drops_identifiers_the_lead_does_not_carry(self, subscriber):
        """Meta scores on identifiers present; nulls would only dilute them."""
        subscriber.phone = None
        subscriber.fbp = None
        subscriber.ip_address = None
        subscriber.fbclid = None
        subscriber.fbc = None
        subscriber.landing_url = None

        event = landing_cms.build_meta_lead_event(subscriber)

        assert set(event["user_data"]) == {
            "email",
            "external_id",
            "client_user_agent",
        }
        assert "event_source_url" not in event

    def test_event_id_is_stable_across_calls(self, subscriber):
        """A retry must deduplicate against the earlier attempt."""
        first = landing_cms.build_meta_lead_event(subscriber)
        second = landing_cms.build_meta_lead_event(subscriber)
        assert first["event_id"] == second["event_id"]


# =============================================================================
# Delivery
# =============================================================================
class TestSendConversion:
    async def test_marks_sent_only_after_meta_accepts_the_event(
        self, wired, subscriber
    ):
        outcome = await landing_cms.send_conversion_to_platforms(42, "meta")

        assert outcome["sent"] is True
        assert outcome["request_id"] == "fbtrace-1"
        assert subscriber.capi_sent is True
        assert wired.session.commits == 1

        connector = FakeConnector.instances[0]
        assert connector.credentials == {
            "pixel_id": "111222333",
            "access_token": "house-token",
        }
        assert len(connector.sent_batches) == 1
        (batch,) = connector.sent_batches
        assert [event["event_id"] for event in batch] == ["landing-lead-42"]

        assert stored_results(subscriber)["sent"] is True

    async def test_does_not_claim_a_send_when_the_connection_fails(
        self, wired, subscriber
    ):
        FakeConnector.connect_result = refused("Invalid access token")

        outcome = await landing_cms.send_conversion_to_platforms(42, "meta")

        assert outcome["sent"] is False
        assert subscriber.capi_sent is False
        assert FakeConnector.instances[0].sent_batches == []
        assert "Invalid access token" in stored_results(subscriber)["reason"]
        assert wired.session.commits == 1

    async def test_does_not_claim_a_send_when_meta_rejects_the_event(
        self, wired, subscriber
    ):
        FakeConnector.send_result = rejected("Invalid parameter")

        outcome = await landing_cms.send_conversion_to_platforms(42, "meta")

        assert outcome["sent"] is False
        assert subscriber.capi_sent is False
        results = stored_results(subscriber)
        assert results["reason"] == "meta rejected the event"
        assert results["errors"] == [{"message": "Invalid parameter"}]

    async def test_a_later_failure_never_clears_a_send_that_happened(
        self, wired, subscriber
    ):
        subscriber.capi_sent = True
        FakeConnector.send_result = rejected()

        await landing_cms.send_conversion_to_platforms(42, "meta")

        assert subscriber.capi_sent is True

    async def test_skips_and_touches_nothing_without_pixel_credentials(
        self, monkeypatch, wired, subscriber
    ):
        monkeypatch.setattr(landing_cms.settings, "meta_pixel_id", None, raising=False)

        outcome = await landing_cms.send_conversion_to_platforms(42, "meta")

        assert outcome == {
            "sent": False,
            "reason": "meta pixel credentials not configured",
        }
        assert subscriber.capi_sent is False
        assert subscriber.capi_results is None
        assert wired.calls == 0
        assert FakeConnector.instances == []

    async def test_skips_a_platform_with_no_connector(self, wired, subscriber):
        outcome = await landing_cms.send_conversion_to_platforms(42, "organic")

        assert outcome == {"sent": False, "reason": "unsupported platform: organic"}
        assert subscriber.capi_sent is False
        assert wired.calls == 0

    async def test_reports_a_missing_subscriber_without_sending(
        self, monkeypatch, wired
    ):
        monkeypatch.setattr(landing_cms, "AsyncSessionLocal", FakeSessionFactory(None))

        outcome = await landing_cms.send_conversion_to_platforms(999, "meta")

        assert outcome == {"sent": False, "reason": "subscriber not found"}
        assert FakeConnector.instances == []

    async def test_an_unexpected_error_never_reports_a_send(
        self, monkeypatch, wired, subscriber
    ):
        class Exploding(FakeConnector):
            async def send_events(self, events):
                raise RuntimeError("boom")

        monkeypatch.setattr(landing_cms, "MetaCAPIConnector", Exploding)

        outcome = await landing_cms.send_conversion_to_platforms(42, "meta")

        assert outcome["sent"] is False
        assert "boom" in outcome["reason"]
        assert subscriber.capi_sent is False


# =============================================================================
# The event as Meta actually receives it
# =============================================================================
class TestRealConnectorPayload:
    """
    Drive the real MetaCAPIConnector over a mock transport, so what is pinned
    is the request Meta would receive - not a fake's record of the call.
    """

    async def test_lead_reaches_the_pixel_events_endpoint(
        self, monkeypatch, subscriber
    ):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json={"id": "111222333", "name": "Stratum"})
            return httpx.Response(
                200, json={"events_received": 1, "fbtrace_id": "tr-1"}
            )

        real_client = httpx.AsyncClient

        def client_factory(*_args: Any, **_kwargs: Any) -> httpx.AsyncClient:
            return real_client(transport=httpx.MockTransport(handler))

        monkeypatch.setattr(platform_connectors.httpx, "AsyncClient", client_factory)

        connector = MetaCAPIConnector()
        connection = await connector.connect(
            {"pixel_id": "111222333", "access_token": "house-token"}
        )
        assert connection.status is ConnectionStatus.CONNECTED

        response = await connector.send_events(
            [landing_cms.build_meta_lead_event(subscriber)]
        )
        assert response.success is True
        assert response.request_id == "tr-1"

        posted = [request for request in requests if request.method == "POST"]
        assert len(posted) == 1
        assert posted[0].url.path.endswith("/111222333/events")

        payload = json.loads(posted[0].content)
        assert payload["access_token"] == "house-token"
        (event,) = payload["data"]

        assert event["event_name"] == "Lead"
        assert event["event_id"] == "landing-lead-42"
        assert event["event_source_url"] == "https://stratumai.app/en"
        assert event["action_source"] == "website"

        user_data = event["user_data"]
        assert user_data["em"] == _sha256("lead@example.com")
        assert user_data["ph"] == _sha256("15551234567")
        # Meta requires these in the clear; hashing them would break matching.
        assert user_data["fbp"] == "fb.1.1700000000000.987654321"
        assert user_data["client_ip_address"] == "203.0.113.10"
        assert user_data["client_user_agent"] == "Mozilla/5.0"
        assert user_data["fbc"].startswith("fb.1.")
        assert user_data["fbc"].endswith(".IwAR-click-id")
        assert "email" not in user_data
        assert "phone" not in user_data

        assert event["custom_data"]["lead_type"] == "landing_page_signup"
        assert event["custom_data"]["utm_campaign"] == "q3-launch"


# =============================================================================
# The endpoint that schedules the send
# =============================================================================
class SignupSession(FakeSession):
    """Session that can also take the INSERT the subscribe endpoint makes."""

    def __init__(self) -> None:
        super().__init__(None)

    def add(self, subscriber: LandingPageSubscriber) -> None:
        subscriber.id = 7
        self.subscriber = subscriber

    async def execute(self, _statement: Any) -> FakeResult:
        # The endpoint's duplicate-email probe runs before the INSERT.
        return FakeResult(None)

    async def refresh(self, _subscriber: LandingPageSubscriber) -> None:
        return None


class TestSubscribeEndpoint:
    """
    The signup endpoint used the session dependency as a context manager, so
    every POST raised ``AttributeError: __aenter__`` and returned 500 - which
    meant the CAPI background task was never even scheduled.
    """

    def test_signup_succeeds_and_schedules_the_capi_send(self, monkeypatch):
        session = SignupSession()
        monkeypatch.setattr(landing_cms, "AsyncSessionLocal", lambda: session)

        scheduled: list[tuple[int, str]] = []

        async def record(subscriber_id: int, platform: str) -> dict[str, Any]:
            scheduled.append((subscriber_id, platform))
            return {"sent": False, "reason": "stubbed"}

        monkeypatch.setattr(landing_cms, "send_conversion_to_platforms", record)

        api = FastAPI()
        api.include_router(landing_cms.router)

        response = TestClient(api).post(
            "/landing-cms/subscribe",
            json={
                "email": "Signup@Example.com",
                "utm_source": "facebook",
                "utm_campaign": "q3-launch",
                "fbclid": "IwAR-click-id",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["attributed_platform"] == "meta"
        assert scheduled == [(7, "meta")]
        assert session.subscriber.email == "signup@example.com"
        assert session.subscriber.capi_sent is False

    def test_organic_signup_schedules_no_send(self, monkeypatch):
        session = SignupSession()
        monkeypatch.setattr(landing_cms, "AsyncSessionLocal", lambda: session)

        scheduled: list[tuple[int, str]] = []

        async def record(subscriber_id: int, platform: str) -> dict[str, Any]:
            scheduled.append((subscriber_id, platform))
            return {}

        monkeypatch.setattr(landing_cms, "send_conversion_to_platforms", record)

        api = FastAPI()
        api.include_router(landing_cms.router)

        response = TestClient(api).post(
            "/landing-cms/subscribe", json={"email": "organic@example.com"}
        )

        assert response.status_code == 200
        assert response.json()["attributed_platform"] == "organic"
        assert scheduled == []
