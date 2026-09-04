# =============================================================================
# Stratum AI - Paddle Bootstrap Script Tests
# =============================================================================
"""
Unit tests for ``scripts_paddle_bootstrap`` and the catalogue / notification
methods it adds to ``PaddleClient``.

No network, no database, no Railway CLI: Paddle is an in-memory fake behind
``httpx.MockTransport`` and ``subprocess.run`` is monkeypatched. Every test
that produces output asserts the webhook secret never reaches stdout/stderr
unless ``--print-webhook-secret`` is given.
"""

import json
import subprocess
from typing import Any

import httpx
import pytest

import scripts_paddle_bootstrap as bootstrap
from app.api.v1.endpoints import paddle_webhook
from app.core.config import settings
from app.core.tiers import SubscriptionTier
from app.services.paddle_service import PaddleClient, pagination_after_cursor

pytestmark = pytest.mark.unit

SANDBOX_KEY = "pdl_sdbx_apikey_" + "a" * 26 + "_" + "b" * 22 + "_" + "ccc"
LIVE_KEY = "pdl_live_apikey_" + "a" * 26 + "_" + "b" * 22 + "_" + "ccc"
SECRET = "pdl_ntfset_" + "d" * 26 + "_" + "e" * 32
WEBHOOK_URL = "https://api.example.com/api/v1/webhooks/paddle"

STARTER = SubscriptionTier.STARTER
PROFESSIONAL = SubscriptionTier.PROFESSIONAL
ENTERPRISE = SubscriptionTier.ENTERPRISE


# =============================================================================
# Fake Paddle API
# =============================================================================


def _product(tier: SubscriptionTier, product_id: str, tagged: bool = True) -> dict[str, Any]:
    entity: dict[str, Any] = {
        "id": product_id,
        "name": bootstrap.product_name(tier),
        "status": "active",
        "tax_category": "standard",
        "custom_data": {"stratum_tier": tier.value} if tagged else None,
    }
    return entity


def _price(
    tier: SubscriptionTier,
    price_id: str,
    product_id: str,
    amount: str = "49900",
    tagged: bool = True,
    interval: str = "month",
    currency: str = "USD",
) -> dict[str, Any]:
    return {
        "id": price_id,
        "product_id": product_id,
        "status": "active",
        "description": f"{tier.value} monthly",
        "unit_price": {"amount": amount, "currency_code": currency},
        "billing_cycle": {"interval": interval, "frequency": 1},
        "custom_data": {"stratum_tier": tier.value} if tagged else None,
    }


def _event_objects(names: list[str]) -> list[dict[str, Any]]:
    """The shape Paddle returns for ``subscribed_events`` (objects, never the request's strings)."""
    return [
        {
            "name": name,
            "description": "",
            "group": name.split(".")[0].title(),
            "available_versions": [1],
        }
        for name in names
    ]


def _setting(
    destination: str,
    events: list[str] | None = None,
    active: bool = True,
    setting_type: str = "url",
) -> dict[str, Any]:
    return {
        "id": "ntfset_existing01",
        "description": "existing",
        "type": setting_type,
        "destination": destination,
        "active": active,
        "api_version": 1,
        "include_sensitive_fields": False,
        "subscribed_events": _event_objects(
            events if events is not None else list(bootstrap.WEBHOOK_EVENTS)
        ),
        "endpoint_secret_key": SECRET,
        "traffic_source": "platform",
    }


class FakePaddle:
    """In-memory Paddle catalogue + notification settings behind ``httpx.MockTransport``."""

    def __init__(
        self,
        products: list[dict[str, Any]] | None = None,
        prices: list[dict[str, Any]] | None = None,
        notification_settings: list[dict[str, Any]] | None = None,
        page_size: int | None = None,
        fail_status: int | None = None,
    ) -> None:
        self.products = list(products or [])
        self.prices = list(prices or [])
        self.notification_settings = list(notification_settings or [])
        self.page_size = page_size
        self.fail_status = fail_status
        self.requests: list[httpx.Request] = []
        self._counter = 0

    # ---------------------------------------------------------------- helpers

    @property
    def posts(self) -> list[httpx.Request]:
        return [request for request in self.requests if request.method == "POST"]

    def post_bodies(self, path: str) -> list[dict[str, Any]]:
        return [
            json.loads(request.content.decode())
            for request in self.posts
            if request.url.path == path
        ]

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter:026d}"

    def _page(self, items: list[dict[str, Any]], request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        start = 0
        if after:
            start = [item["id"] for item in items].index(after) + 1
        size = self.page_size or max(len(items), 1)
        chunk = items[start : start + size]
        has_more = start + size < len(items)
        next_url = (
            f"https://sandbox-api.paddle.com{request.url.path}?after={chunk[-1]['id']}"
            if has_more and chunk
            else None
        )
        return httpx.Response(
            200,
            json={
                "data": chunk,
                "meta": {
                    "request_id": "req_1",
                    "pagination": {
                        "per_page": size,
                        "next": next_url,
                        "has_more": has_more,
                        "estimated_total": len(items),
                    },
                },
            },
        )

    # ---------------------------------------------------------------- handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_status is not None:
            return httpx.Response(
                self.fail_status,
                json={"error": {"type": "request_error", "code": "authentication_malformed", "detail": "bad key"}},
            )
        path = request.url.path
        params = request.url.params
        if request.method == "GET" and path == "/products":
            status = params.get("status", "active")
            return self._page([p for p in self.products if p["status"] == status], request)
        if request.method == "POST" and path == "/products":
            body = json.loads(request.content.decode())
            product = {"id": self._next_id("pro"), "status": "active", **body}
            self.products.append(product)
            return httpx.Response(201, json={"data": product, "meta": {"request_id": "req_2"}})
        if request.method == "GET" and path == "/prices":
            status = params.get("status", "active")
            product_id = params.get("product_id")
            items = [
                p
                for p in self.prices
                if p["status"] == status and (not product_id or p["product_id"] == product_id)
            ]
            return self._page(items, request)
        if request.method == "POST" and path == "/prices":
            body = json.loads(request.content.decode())
            price = {"id": self._next_id("pri"), "status": "active", **body}
            self.prices.append(price)
            return httpx.Response(201, json={"data": price, "meta": {"request_id": "req_3"}})
        if request.method == "GET" and path == "/notification-settings":
            return self._page(self.notification_settings, request)
        if request.method == "POST" and path == "/notification-settings":
            body = json.loads(request.content.decode())
            # Paddle echoes the request but renders subscribed_events as objects.
            setting = {
                "id": self._next_id("ntfset"),
                "active": True,
                "traffic_source": "platform",
                "endpoint_secret_key": SECRET,
                **body,
                "subscribed_events": _event_objects(list(body.get("subscribed_events", []))),
            }
            self.notification_settings.append(setting)
            return httpx.Response(201, json={"data": setting, "meta": {"request_id": "req_4"}})
        return httpx.Response(
            404, json={"error": {"code": "not_found", "detail": f"{request.method} {path}"}}
        )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sandbox_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sandbox key plus Railway-style placeholder price ids on the live settings object."""
    monkeypatch.setattr(settings, "paddle_api_key", SANDBOX_KEY)
    monkeypatch.setattr(settings, "paddle_environment", "sandbox")
    monkeypatch.setattr(settings, "paddle_webhook_secret", None)
    monkeypatch.setattr(settings, "paddle_starter_price_id", "pri_…")
    monkeypatch.setattr(settings, "paddle_professional_price_id", "pri_…")
    monkeypatch.setattr(settings, "paddle_enterprise_price_id", "pri_…")


@pytest.fixture
def production_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live key with ``PADDLE_ENVIRONMENT=production`` and placeholder price ids."""
    monkeypatch.setattr(settings, "paddle_api_key", LIVE_KEY)
    monkeypatch.setattr(settings, "paddle_environment", "production")
    monkeypatch.setattr(settings, "paddle_webhook_secret", None)
    monkeypatch.setattr(settings, "paddle_starter_price_id", "pri_…")
    monkeypatch.setattr(settings, "paddle_professional_price_id", "pri_…")
    monkeypatch.setattr(settings, "paddle_enterprise_price_id", "pri_…")


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Fail loudly if the script ever shells out unexpectedly; records argv otherwise."""
    calls: list[list[str]] = []

    def _forbidden(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(args[0]))
        raise AssertionError("subprocess.run must not be called in this test")

    monkeypatch.setattr(subprocess, "run", _forbidden)
    return calls


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakePaddle) -> FakePaddle:
    """Route ``build_client`` through the fake transport (settings supply key + environment)."""
    monkeypatch.setattr(
        bootstrap,
        "build_client",
        lambda: PaddleClient(transport=httpx.MockTransport(fake)),
    )
    return fake


def _assert_no_secret(captured: pytest.CaptureFixture[str]) -> None:
    out, err = captured.readouterr()
    assert SECRET not in out
    assert SECRET not in err
    assert SANDBOX_KEY not in out
    assert SANDBOX_KEY not in err


# =============================================================================
# Guards and usage
# =============================================================================


class TestGuards:
    @pytest.mark.parametrize("key", [None, "", "…", "pdl_sdbx_apikey_…", "short_key", "pdl_sdbx_apikey_..."])
    def test_placeholder_key_is_refused(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
        key: str | None,
    ) -> None:
        monkeypatch.setattr(settings, "paddle_api_key", key)
        fake = _install(monkeypatch, FakePaddle())
        assert bootstrap.main([]) == bootstrap.EXIT_CONFIG
        assert fake.requests == []
        out, err = capsys.readouterr()
        assert "placeholder" in err
        assert out == ""

    def test_live_key_on_sandbox_environment_is_refused(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(settings, "paddle_api_key", LIVE_KEY)
        fake = _install(monkeypatch, FakePaddle())
        assert bootstrap.main([]) == bootstrap.EXIT_CONFIG
        assert fake.requests == []
        _, err = capsys.readouterr()
        assert "production key" in err
        assert "PADDLE_ENVIRONMENT=sandbox" in err
        assert LIVE_KEY not in err

    def test_sandbox_key_on_production_environment_is_refused(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(settings, "paddle_environment", "production")
        fake = _install(monkeypatch, FakePaddle())
        assert bootstrap.main([]) == bootstrap.EXIT_CONFIG
        assert fake.requests == []
        _, err = capsys.readouterr()
        assert "sandbox key" in err

    def test_unknown_prefix_only_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(settings, "paddle_api_key", "legacy_" + "x" * 40)
        fake = _install(monkeypatch, FakePaddle())
        assert bootstrap.main([]) == bootstrap.EXIT_OK
        assert fake.requests
        out, _ = capsys.readouterr()
        assert "warning  PADDLE_API_KEY does not start with pdl_sdbx_apikey_" in out

    def test_check_key_environment_table(self) -> None:
        assert bootstrap.check_key_environment(SANDBOX_KEY, "sandbox") == (None, None)
        assert bootstrap.check_key_environment(LIVE_KEY, "production") == (None, None)
        error, warning = bootstrap.check_key_environment(LIVE_KEY, "sandbox")
        assert error and warning is None
        error, warning = bootstrap.check_key_environment("something_else_entirely", "production")
        assert error is None and warning and "pdl_live_apikey_" in warning


class TestEnvironmentBoundary:
    """A sandbox run must never write into the production service and vice versa."""

    def test_sandbox_run_refuses_production_railway_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(monkeypatch, FakePaddle())
        code = bootstrap.main(
            ["--webhook-url", WEBHOOK_URL, "--railway-env", "production", "--railway-service", "api"]
        )
        assert code == bootstrap.EXIT_CONFIG
        assert fake.requests == []
        assert no_subprocess == []
        out, err = capsys.readouterr()
        assert out == ""
        assert "--railway-env production does not match PADDLE_ENVIRONMENT=sandbox" in err
        assert "sandbox price ids and a sandbox webhook secret" in err
        assert SANDBOX_KEY not in err

    def test_production_run_refuses_non_production_railway_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        production_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(monkeypatch, FakePaddle())
        code = bootstrap.main(["--dry-run", "--railway-env", "staging", "--railway-service", "api"])
        assert code == bootstrap.EXIT_CONFIG
        assert fake.requests == []
        assert no_subprocess == []
        _, err = capsys.readouterr()
        assert "--railway-env staging does not match PADDLE_ENVIRONMENT=production" in err
        assert LIVE_KEY not in err

    def test_check_railway_environment_table(self) -> None:
        assert bootstrap.check_railway_environment("staging", "sandbox") is None
        assert bootstrap.check_railway_environment("production", "production") is None
        assert bootstrap.check_railway_environment(" Production ", "production") is None
        assert bootstrap.check_railway_environment("production", "sandbox")
        assert bootstrap.check_railway_environment("staging", "production")


class TestUsage:
    def test_railway_flags_must_be_paired(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(monkeypatch, FakePaddle())
        assert bootstrap.main(["--railway-env", "staging"]) == bootstrap.EXIT_USAGE
        assert bootstrap.main(["--railway-service", "api"]) == bootstrap.EXIT_USAGE
        assert fake.requests == []
        _, err = capsys.readouterr()
        assert "--railway-env and --railway-service must be given together" in err

    def test_webhook_url_must_be_https(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
    ) -> None:
        fake = _install(monkeypatch, FakePaddle())
        assert bootstrap.main(["--webhook-url", "http://api.example.com/api/v1/webhooks/paddle"]) == bootstrap.EXIT_USAGE
        assert fake.requests == []

    def test_enterprise_usd_must_be_positive(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
    ) -> None:
        fake = _install(monkeypatch, FakePaddle())
        assert bootstrap.main(["--enterprise-usd", "0"]) == bootstrap.EXIT_USAGE
        assert fake.requests == []

    def test_help_does_not_need_paddle(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            bootstrap.main(["--help"])
        assert excinfo.value.code == 0
        out, _ = capsys.readouterr()
        assert "--enterprise-usd" in out
        assert "--print-webhook-secret" in out


# =============================================================================
# Catalogue
# =============================================================================


class TestCatalogue:
    def test_creates_missing_products_and_prices(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(monkeypatch, FakePaddle())
        assert bootstrap.main([]) == bootstrap.EXIT_OK

        products = fake.post_bodies("/products")
        assert [p["name"] for p in products] == [
            "Stratum AI Starter",
            "Stratum AI Professional",
            "Stratum AI Enterprise",
        ]
        assert all(p["tax_category"] == "standard" for p in products)
        assert [p["custom_data"] for p in products] == [
            {"stratum_tier": "starter"},
            {"stratum_tier": "professional"},
            {"stratum_tier": "enterprise"},
        ]
        assert products[0]["description"] == "For teams scaling their ad operations"

        prices = fake.post_bodies("/prices")
        assert len(prices) == 2
        starter, professional = prices
        assert starter["unit_price"] == {"amount": "49900", "currency_code": "USD"}
        assert professional["unit_price"] == {"amount": "99900", "currency_code": "USD"}
        assert starter["billing_cycle"] == {"interval": "month", "frequency": 1}
        assert starter["quantity"] == {"minimum": 1, "maximum": 1}
        assert starter["description"] == "Starter monthly"
        assert professional["description"] == "Professional monthly"
        assert starter["custom_data"] == {"stratum_tier": "starter"}
        assert starter["product_id"] == fake.products[0]["id"]
        assert professional["product_id"] == fake.products[1]["id"]

        out, _ = capsys.readouterr()
        assert f"{fake.products[0]['id']} created" in out
        assert f"{fake.prices[0]['id']} created" in out
        assert "PADDLE_STARTER_PRICE_ID=" + fake.prices[0]["id"] in out
        assert "PADDLE_PROFESSIONAL_PRICE_ID=" + fake.prices[1]["id"] in out
        assert "railway variable set -e <environment> --service <service> --skip-deploys" in out

    def test_reuses_existing_product_and_price(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(
            monkeypatch,
            FakePaddle(
                products=[
                    _product(STARTER, "pro_starter"),
                    _product(PROFESSIONAL, "pro_pro"),
                    _product(ENTERPRISE, "pro_ent"),
                ],
                prices=[
                    _price(STARTER, "pri_starter", "pro_starter", "49900"),
                    _price(PROFESSIONAL, "pri_pro", "pro_pro", "99900"),
                ],
            ),
        )
        assert bootstrap.main([]) == bootstrap.EXIT_OK
        assert fake.posts == []
        out, _ = capsys.readouterr()
        assert "pro_starter reused" in out
        assert "pri_starter reused  49900 USD minor units / month" in out
        assert "pri_pro reused  99900 USD minor units / month" in out
        assert "PADDLE_STARTER_PRICE_ID=pri_starter" in out
        assert "placeholder -> pri_starter" in out
        assert "warning  " not in out

    def test_price_lookup_is_scoped_to_the_product(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
    ) -> None:
        fake = _install(monkeypatch, FakePaddle(products=[_product(STARTER, "pro_starter")]))
        bootstrap.main([])
        price_lists = [r for r in fake.requests if r.method == "GET" and r.url.path == "/prices"]
        assert price_lists
        assert all(r.url.params["product_id"] for r in price_lists)
        assert price_lists[0].url.params["product_id"] == "pro_starter"
        assert price_lists[0].url.params["status"] == "active"

    def test_product_name_fallback_when_custom_data_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(
            monkeypatch,
            FakePaddle(products=[_product(STARTER, "pro_by_name", tagged=False)]),
        )
        assert bootstrap.main([]) == bootstrap.EXIT_OK
        assert [p["name"] for p in fake.post_bodies("/products")] == [
            "Stratum AI Professional",
            "Stratum AI Enterprise",
        ]
        out, _ = capsys.readouterr()
        assert "pro_by_name reused" in out

    def test_untagged_price_with_same_amount_is_reused(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(
            monkeypatch,
            FakePaddle(
                products=[_product(STARTER, "pro_starter")],
                prices=[
                    _price(STARTER, "pri_yearly", "pro_starter", "49900", tagged=False, interval="year"),
                    _price(STARTER, "pri_eur", "pro_starter", "49900", tagged=False, currency="EUR"),
                    _price(STARTER, "pri_other_amount", "pro_starter", "12300", tagged=False),
                    _price(STARTER, "pri_manual", "pro_starter", "49900", tagged=False),
                ],
            ),
        )
        assert bootstrap.main([]) == bootstrap.EXIT_OK
        starter_prices = [
            body for body in fake.post_bodies("/prices") if body["custom_data"] == {"stratum_tier": "starter"}
        ]
        assert starter_prices == []
        out, _ = capsys.readouterr()
        assert "pri_manual reused" in out
        # an untagged price with another amount is not "stale" (only tagged ones are reported)
        assert "warning  " not in out

    @pytest.mark.parametrize("dry_run", [False, True])
    def test_tagged_price_with_other_amount_is_not_reused(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
        dry_run: bool,
    ) -> None:
        fake = _install(
            monkeypatch,
            FakePaddle(
                products=[_product(STARTER, "pro_starter")],
                prices=[_price(STARTER, "pri_wrong_amount", "pro_starter", "12300")],
            ),
        )
        assert bootstrap.main(["--dry-run"] if dry_run else []) == bootstrap.EXIT_OK
        starter_prices = [
            body for body in fake.post_bodies("/prices") if body["custom_data"] == {"stratum_tier": "starter"}
        ]
        out, _ = capsys.readouterr()
        assert "pri_wrong_amount reused" not in out
        assert "12300 USD minor units" not in out
        if dry_run:
            assert starter_prices == []
            assert "<new> would create  49900 USD minor units / month" in out
            assert "a price with the catalogue amount would be created" in out
        else:
            assert len(starter_prices) == 1
            assert starter_prices[0]["unit_price"] == {"amount": "49900", "currency_code": "USD"}
            created = next(
                p
                for p in fake.prices
                if p["id"] != "pri_wrong_amount" and p["custom_data"] == {"stratum_tier": "starter"}
            )
            assert f"{created['id']} created  49900 USD minor units / month" in out
            assert f"PADDLE_STARTER_PRICE_ID={created['id']}" in out
            assert "a price with the catalogue amount was created" in out
        assert (
            "warning  starter price pri_wrong_amount carries the stratum_tier tag but its amount is 12300, "
            "not the catalogue's 49900"
        ) in out
        assert "Archive pri_wrong_amount in the Paddle dashboard" in out

    def test_enterprise_amount_change_creates_a_new_price_then_reuses_it(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(
            monkeypatch,
            FakePaddle(
                products=[_product(ENTERPRISE, "pro_ent")],
                prices=[_price(ENTERPRISE, "pri_ent_old", "pro_ent", "250000")],
            ),
        )
        assert bootstrap.main(["--enterprise-usd", "3000"]) == bootstrap.EXIT_OK
        enterprise = [b for b in fake.post_bodies("/prices") if b["custom_data"] == {"stratum_tier": "enterprise"}]
        assert len(enterprise) == 1
        assert enterprise[0]["unit_price"] == {"amount": "300000", "currency_code": "USD"}
        out, _ = capsys.readouterr()
        assert (
            "warning  enterprise price pri_ent_old carries the stratum_tier tag but its amount is 250000, "
            "not the catalogue's 300000"
        ) in out
        assert "or restore TIER_PRICING / --enterprise-usd" in out

        # Second run with the same amount reuses the new price and creates nothing.
        posts_before = len(fake.posts)
        assert bootstrap.main(["--enterprise-usd", "3000"]) == bootstrap.EXIT_OK
        assert len(fake.posts) == posts_before
        out, _ = capsys.readouterr()
        assert f"{fake.prices[-1]['id']} reused  300000 USD minor units / month" in out
        assert "pri_ent_old carries the stratum_tier tag" in out  # still stale, still reported

    def test_pagination_is_followed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
    ) -> None:
        fake = _install(
            monkeypatch,
            FakePaddle(
                products=[
                    _product(STARTER, "pro_starter"),
                    _product(PROFESSIONAL, "pro_pro"),
                    _product(ENTERPRISE, "pro_ent"),
                ],
                page_size=1,
            ),
        )
        assert bootstrap.main([]) == bootstrap.EXIT_OK
        product_pages = [r for r in fake.requests if r.method == "GET" and r.url.path == "/products"]
        assert [r.url.params.get("after") for r in product_pages] == [None, "pro_starter", "pro_pro"]
        assert fake.post_bodies("/products") == []

    def test_enterprise_price_skipped_without_flag(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(monkeypatch, FakePaddle())
        assert bootstrap.main([]) == bootstrap.EXIT_OK
        assert all(body["custom_data"]["stratum_tier"] != "enterprise" for body in fake.post_bodies("/prices"))
        out, _ = capsys.readouterr()
        assert "skipped: custom pricing (pass --enterprise-usd <int>)" in out
        assert "PADDLE_ENTERPRISE_PRICE_ID was left untouched" in out
        assert "settings.paddle_fully_configured" in out
        assert "APP_ENV=production" in out
        assert "PADDLE_ENTERPRISE_PRICE_ID=" not in out

    def test_enterprise_price_created_with_flag(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(monkeypatch, FakePaddle())
        assert bootstrap.main(["--enterprise-usd", "2500"]) == bootstrap.EXIT_OK
        enterprise = [b for b in fake.post_bodies("/prices") if b["custom_data"] == {"stratum_tier": "enterprise"}]
        assert len(enterprise) == 1
        assert enterprise[0]["unit_price"] == {"amount": "250000", "currency_code": "USD"}
        assert enterprise[0]["description"] == "Enterprise monthly"
        out, _ = capsys.readouterr()
        assert "PADDLE_ENTERPRISE_PRICE_ID=" + fake.prices[-1]["id"] in out
        assert "was left untouched" not in out

    def test_paddle_error_exits_with_paddle_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _install(monkeypatch, FakePaddle(fail_status=401))
        assert bootstrap.main([]) == bootstrap.EXIT_PADDLE
        out, err = capsys.readouterr()
        assert "Paddle API error 401 authentication_malformed" in err
        assert SANDBOX_KEY not in err
        # stdout carries only the PaddleClient log line, never the key or a report
        assert SANDBOX_KEY not in out
        assert "Paddle bootstrap - environment" not in out


# =============================================================================
# Webhook destination
# =============================================================================


class TestWebhook:
    def test_events_match_the_handler(self) -> None:
        handled = (
            set(paddle_webhook.SUBSCRIPTION_SYNC_EVENTS)
            | {paddle_webhook.SUBSCRIPTION_CANCELED_EVENT}
            | set(paddle_webhook.TRANSACTION_SYNC_EVENTS)
            | {paddle_webhook.TRANSACTION_FAILED_EVENT}
            | set(paddle_webhook.CUSTOMER_EVENTS)
        )
        assert set(bootstrap.WEBHOOK_EVENTS) == handled
        assert len(bootstrap.WEBHOOK_EVENTS) == len(handled)
        assert "subscription.canceled" in bootstrap.WEBHOOK_EVENTS

    def test_event_names_accept_paddle_objects_and_strings(self) -> None:
        assert bootstrap._event_names(_event_objects(["transaction.paid", "customer.created"])) == {
            "transaction.paid",
            "customer.created",
        }
        assert bootstrap._event_names(["a.b", {"name": "c.d"}, None, {}, ""]) == {"a.b", "c.d"}
        assert bootstrap._event_names(None) == set()
        assert bootstrap._event_names("a.b") == set()

    def test_created_with_expected_payload_and_secret_hidden(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(monkeypatch, FakePaddle())
        assert bootstrap.main(["--webhook-url", WEBHOOK_URL]) == bootstrap.EXIT_OK
        bodies = fake.post_bodies("/notification-settings")
        assert len(bodies) == 1
        body = bodies[0]
        assert body["destination"] == WEBHOOK_URL
        assert body["type"] == "url"
        assert body["description"] == bootstrap.WEBHOOK_DESCRIPTION
        assert body["subscribed_events"] == list(bootstrap.WEBHOOK_EVENTS)
        assert body["include_sensitive_fields"] is False
        assert body["api_version"] == 1
        out, err = capsys.readouterr()
        assert f"{fake.notification_settings[0]['id']} created  {WEBHOOK_URL}" in out
        assert "PADDLE_WEBHOOK_SECRET: NOT printed" in out
        assert SECRET not in out
        assert SECRET not in err

    def test_reused_by_destination(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(monkeypatch, FakePaddle(notification_settings=[_setting(WEBHOOK_URL)]))
        assert bootstrap.main(["--webhook-url", WEBHOOK_URL]) == bootstrap.EXIT_OK
        assert fake.post_bodies("/notification-settings") == []
        out, err = capsys.readouterr()
        assert "ntfset_existing01 reused" in out
        assert "destination already existed" in out
        # Paddle lists subscribed_events as objects; a fully subscribed destination must not warn.
        assert "is not subscribed to" not in out
        assert "warning  " not in out
        assert SECRET not in out
        assert SECRET not in err

    def test_reused_destination_missing_events_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        events = [e for e in bootstrap.WEBHOOK_EVENTS if e != "subscription.canceled"]
        _install(monkeypatch, FakePaddle(notification_settings=[_setting(WEBHOOK_URL, events)]))
        assert bootstrap.main(["--webhook-url", WEBHOOK_URL]) == bootstrap.EXIT_OK
        out, _ = capsys.readouterr()
        assert "warning  destination ntfset_existing01 is not subscribed to subscription.canceled" in out
        assert "customer.created" not in out.split("warning  destination")[1]

    def test_reused_inactive_destination_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(
            monkeypatch, FakePaddle(notification_settings=[_setting(WEBHOOK_URL, active=False)])
        )
        assert bootstrap.main(["--webhook-url", WEBHOOK_URL]) == bootstrap.EXIT_OK
        assert fake.post_bodies("/notification-settings") == []
        out, err = capsys.readouterr()
        assert f"ntfset_existing01 reused  {WEBHOOK_URL}  [INACTIVE - see warning]" in out
        assert "warning  destination ntfset_existing01 is inactive (active=false)" in out
        assert "Developer Tools > Notifications" in out
        assert SECRET not in out
        assert SECRET not in err

    def test_non_url_destination_is_not_reused(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
    ) -> None:
        fake = _install(
            monkeypatch,
            FakePaddle(notification_settings=[_setting(WEBHOOK_URL, setting_type="email")]),
        )
        assert bootstrap.main(["--webhook-url", WEBHOOK_URL]) == bootstrap.EXIT_OK
        assert len(fake.post_bodies("/notification-settings")) == 1

    def test_other_destinations_do_not_match(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
    ) -> None:
        fake = _install(
            monkeypatch,
            FakePaddle(notification_settings=[_setting("https://other.example.com/api/v1/webhooks/paddle")]),
        )
        assert bootstrap.main(["--webhook-url", WEBHOOK_URL]) == bootstrap.EXIT_OK
        assert len(fake.post_bodies("/notification-settings")) == 1

    def test_unexpected_path_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _install(monkeypatch, FakePaddle())
        assert bootstrap.main(["--webhook-url", "https://api.example.com/hooks"]) == bootstrap.EXIT_OK
        out, _ = capsys.readouterr()
        assert "warning  --webhook-url path is /hooks" in out

    def test_print_webhook_secret_is_opt_in(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _install(monkeypatch, FakePaddle())
        assert bootstrap.main(["--webhook-url", WEBHOOK_URL, "--print-webhook-secret"]) == bootstrap.EXIT_OK
        out, err = capsys.readouterr()
        assert out.count(SECRET) == 1
        assert f"PADDLE_WEBHOOK_SECRET={SECRET}" in out
        assert "WARNING  printing the webhook endpoint secret" in out
        assert SECRET not in err

    def test_print_webhook_secret_works_for_reused_destination(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _install(monkeypatch, FakePaddle(notification_settings=[_setting(WEBHOOK_URL)]))
        assert bootstrap.main(["--webhook-url", WEBHOOK_URL, "--print-webhook-secret"]) == bootstrap.EXIT_OK
        out, _ = capsys.readouterr()
        assert out.count(SECRET) == 1


# =============================================================================
# Dry run
# =============================================================================


class TestDryRun:
    def test_dry_run_performs_no_post_and_no_cli_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(monkeypatch, FakePaddle(products=[_product(STARTER, "pro_starter")]))
        code = bootstrap.main(
            [
                "--dry-run",
                "--webhook-url",
                WEBHOOK_URL,
                "--enterprise-usd",
                "3000",
                "--railway-env",
                "staging",
                "--railway-service",
                "api",
            ]
        )
        assert code == bootstrap.EXIT_OK
        assert fake.posts == []
        assert all(r.method == "GET" for r in fake.requests)
        assert no_subprocess == []
        out, err = capsys.readouterr()
        assert "[DRY RUN - nothing was created]" in out
        assert "pro_starter reused" in out
        assert "<new> would create" in out
        assert "Railway (dry run) would run:" in out
        assert (
            "railway variable set -e staging --service api --skip-deploys "
            "PADDLE_STARTER_PRICE_ID=<new> PADDLE_PROFESSIONAL_PRICE_ID=<new> "
            "PADDLE_ENTERPRISE_PRICE_ID=<new>\n"
        ) in out
        assert (
            "railway variable set -e staging --service api --skip-deploys PADDLE_WEBHOOK_SECRET --stdin"
            "   (value piped through stdin: <redacted>)"
        ) in out
        assert "PADDLE_WEBHOOK_SECRET=" not in out
        assert "PADDLE_WEBHOOK_SECRET: would be created with the destination (dry run)." in out
        assert SECRET not in out
        assert SECRET not in err

    def test_dry_run_with_everything_present_shows_railway_line(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        no_subprocess: list[list[str]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(
            monkeypatch,
            FakePaddle(
                products=[_product(STARTER, "pro_starter"), _product(PROFESSIONAL, "pro_pro")],
                prices=[
                    _price(STARTER, "pri_starter", "pro_starter", "49900"),
                    _price(PROFESSIONAL, "pri_pro", "pro_pro", "99900"),
                ],
                notification_settings=[_setting(WEBHOOK_URL)],
            ),
        )
        code = bootstrap.main(
            ["--dry-run", "--webhook-url", WEBHOOK_URL, "--railway-env", "staging", "--railway-service", "api"]
        )
        assert code == bootstrap.EXIT_OK
        assert fake.posts == []
        out, _ = capsys.readouterr()
        assert (
            "railway variable set -e staging --service api --skip-deploys "
            "PADDLE_STARTER_PRICE_ID=pri_starter PADDLE_PROFESSIONAL_PRICE_ID=pri_pro"
        ) in out
        assert "--stdin" not in out
        assert "PADDLE_WEBHOOK_SECRET=" not in out


# =============================================================================
# Railway CLI
# =============================================================================


RAILWAY_PREFIX = ["railway", "variable", "set", "-e", "staging", "--service", "api", "--skip-deploys"]


class TestRailway:
    @pytest.fixture
    def recorded_run(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        def _fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append({"argv": list(argv), "kwargs": kwargs})
            return subprocess.CompletedProcess(argv, 0, stdout="Set variables\n", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        return calls

    def test_writes_price_ids_as_arguments_and_the_secret_through_stdin(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        recorded_run: list[dict[str, Any]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = _install(monkeypatch, FakePaddle())
        code = bootstrap.main(
            ["--webhook-url", WEBHOOK_URL, "--railway-env", "staging", "--railway-service", "api"]
        )
        assert code == bootstrap.EXIT_OK
        assert len(recorded_run) == 2
        price_call, secret_call = recorded_run
        assert price_call["argv"] == [
            *RAILWAY_PREFIX,
            f"PADDLE_STARTER_PRICE_ID={fake.prices[0]['id']}",
            f"PADDLE_PROFESSIONAL_PRICE_ID={fake.prices[1]['id']}",
        ]
        assert "input" not in price_call["kwargs"]
        assert secret_call["argv"] == [*RAILWAY_PREFIX, "PADDLE_WEBHOOK_SECRET", "--stdin"]
        assert secret_call["kwargs"]["input"] == SECRET
        for call in recorded_run:
            assert call["kwargs"].get("shell") in (None, False)
            assert call["kwargs"]["capture_output"] is True
            assert call["kwargs"]["text"] is True
            assert all(SECRET not in arg for arg in call["argv"])
        out, err = capsys.readouterr()
        assert "PADDLE_WEBHOOK_SECRET: will be piped to `railway variable set PADDLE_WEBHOOK_SECRET --stdin`" in out
        assert (
            "Railway: wrote PADDLE_STARTER_PRICE_ID, PADDLE_PROFESSIONAL_PRICE_ID, PADDLE_WEBHOOK_SECRET "
            "to service 'api' in environment 'staging' (--skip-deploys; the secret was piped through --stdin)."
        ) in out
        assert "railway redeploy -e staging --service api" in out
        assert SECRET not in out
        assert SECRET not in err

    def test_production_run_reused_webhook_secret_is_not_written(
        self,
        monkeypatch: pytest.MonkeyPatch,
        production_settings: None,
        recorded_run: list[dict[str, Any]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _install(
            monkeypatch,
            FakePaddle(
                products=[_product(STARTER, "pro_starter"), _product(PROFESSIONAL, "pro_pro")],
                prices=[
                    _price(STARTER, "pri_starter", "pro_starter", "49900"),
                    _price(PROFESSIONAL, "pri_pro", "pro_pro", "99900"),
                ],
                notification_settings=[_setting(WEBHOOK_URL)],
            ),
        )
        code = bootstrap.main(
            ["--webhook-url", WEBHOOK_URL, "--railway-env", "production", "--railway-service", "api"]
        )
        assert code == bootstrap.EXIT_OK
        assert len(recorded_run) == 1
        assert recorded_run[0]["argv"] == [
            "railway",
            "variable",
            "set",
            "-e",
            "production",
            "--service",
            "api",
            "--skip-deploys",
            "PADDLE_STARTER_PRICE_ID=pri_starter",
            "PADDLE_PROFESSIONAL_PRICE_ID=pri_pro",
        ]
        out, err = capsys.readouterr()
        assert "environment: production (https://api.paddle.com)" in out
        assert "Railway: wrote PADDLE_STARTER_PRICE_ID, PADDLE_PROFESSIONAL_PRICE_ID to service 'api'" in out
        assert "--stdin" not in out
        assert SECRET not in out
        assert SECRET not in err
        assert LIVE_KEY not in out

    def test_cli_failure_is_reported_without_the_secret(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _failing_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr=f"Error: could not set PADDLE_WEBHOOK_SECRET={SECRET}"
            )

        monkeypatch.setattr(subprocess, "run", _failing_run)
        _install(monkeypatch, FakePaddle())
        code = bootstrap.main(
            ["--webhook-url", WEBHOOK_URL, "--railway-env", "staging", "--railway-service", "api"]
        )
        assert code == bootstrap.EXIT_RAILWAY
        out, err = capsys.readouterr()
        assert "railway variable set exited with 1" in err
        assert "(while writing PADDLE_STARTER_PRICE_ID, PADDLE_PROFESSIONAL_PRICE_ID)" in err
        assert "<redacted>" in err
        # Nothing was written, so the report must not claim otherwise.
        assert "Railway: wrote" not in out
        assert "were written" not in out
        assert "Railway: PADDLE_WEBHOOK_SECRET was NOT written" in out
        assert "--print-webhook-secret" in out
        assert SECRET not in err
        assert SECRET not in out

    def test_secret_write_failure_after_price_ids_reports_the_partial_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []

        def _stdin_fails(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append(list(argv))
            if "--stdin" in argv:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr=f"boom {SECRET}")
            return subprocess.CompletedProcess(argv, 0, stdout="Set variables\n", stderr="")

        monkeypatch.setattr(subprocess, "run", _stdin_fails)
        _install(monkeypatch, FakePaddle())
        code = bootstrap.main(
            ["--webhook-url", WEBHOOK_URL, "--railway-env", "staging", "--railway-service", "api"]
        )
        assert code == bootstrap.EXIT_RAILWAY
        assert len(calls) == 2
        out, err = capsys.readouterr()
        assert "(while piping PADDLE_WEBHOOK_SECRET through --stdin)" in err
        assert "<redacted>" in err
        assert (
            "Railway: PADDLE_STARTER_PRICE_ID, PADDLE_PROFESSIONAL_PRICE_ID were written to service 'api' "
            "in environment 'staging' before the failure."
        ) in out
        assert "Railway: PADDLE_WEBHOOK_SECRET was NOT written" in out
        assert "Railway: wrote" not in out
        assert SECRET not in err
        assert SECRET not in out

    def test_missing_cli_is_reported(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sandbox_settings: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _missing(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("railway")

        monkeypatch.setattr(subprocess, "run", _missing)
        _install(monkeypatch, FakePaddle())
        assert bootstrap.main(["--railway-env", "staging", "--railway-service", "api"]) == bootstrap.EXIT_RAILWAY
        _, err = capsys.readouterr()
        assert "railway CLI not found" in err

    def test_railway_stdin_argv_shape(self) -> None:
        assert bootstrap.railway_stdin_argv("staging", "api", "PADDLE_WEBHOOK_SECRET") == [
            *RAILWAY_PREFIX,
            "PADDLE_WEBHOOK_SECRET",
            "--stdin",
        ]


# =============================================================================
# PaddleClient additions
# =============================================================================


class TestPaddleClientCatalogue:
    async def test_list_prices_without_filter_keeps_the_old_query(self) -> None:
        fake = FakePaddle(prices=[_price(STARTER, "pri_1", "pro_1")])
        client = PaddleClient(api_key=SANDBOX_KEY, environment="sandbox", transport=httpx.MockTransport(fake))
        prices = await client.list_prices()
        await client.aclose()
        assert [p["id"] for p in prices] == ["pri_1"]
        assert str(fake.requests[0].url) == "https://sandbox-api.paddle.com/prices?status=active"

    async def test_list_products_follows_every_page(self) -> None:
        fake = FakePaddle(
            products=[_product(STARTER, "pro_1"), _product(PROFESSIONAL, "pro_2"), _product(ENTERPRISE, "pro_3")],
            page_size=2,
        )
        client = PaddleClient(api_key=SANDBOX_KEY, environment="sandbox", transport=httpx.MockTransport(fake))
        products = await client.list_products()
        await client.aclose()
        assert [p["id"] for p in products] == ["pro_1", "pro_2", "pro_3"]
        assert [r.url.params.get("after") for r in fake.requests] == [None, "pro_2"]

    async def test_get_all_stops_on_repeated_cursor(self) -> None:
        def _looping(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "pro_loop"}],
                    "meta": {"pagination": {"has_more": True, "next": "https://x/products?after=pro_loop"}},
                },
            )

        client = PaddleClient(api_key=SANDBOX_KEY, environment="sandbox", transport=httpx.MockTransport(_looping))
        products = await client.list_products()
        await client.aclose()
        assert len(products) == 2  # first page + the single repeated cursor page

    async def test_create_notification_setting_payload(self) -> None:
        fake = FakePaddle()
        client = PaddleClient(api_key=SANDBOX_KEY, environment="sandbox", transport=httpx.MockTransport(fake))
        setting = await client.create_notification_setting(
            description="d", destination=WEBHOOK_URL, subscribed_events=["subscription.created"]
        )
        await client.aclose()
        assert setting["endpoint_secret_key"] == SECRET
        assert setting["subscribed_events"] == _event_objects(["subscription.created"])
        body = fake.post_bodies("/notification-settings")[0]
        assert body == {
            "description": "d",
            "destination": WEBHOOK_URL,
            "type": "url",
            "subscribed_events": ["subscription.created"],
            "include_sensitive_fields": False,
            "api_version": 1,
        }

    def test_pagination_after_cursor(self) -> None:
        assert pagination_after_cursor("https://api.paddle.com/prices?after=pri_01abc") == "pri_01abc"
        assert pagination_after_cursor("https://api.paddle.com/prices?per_page=5&after=pri_x") == "pri_x"
        assert pagination_after_cursor("https://api.paddle.com/prices") is None
        assert pagination_after_cursor(None) is None
        assert pagination_after_cursor("") is None
        assert pagination_after_cursor(123) is None
