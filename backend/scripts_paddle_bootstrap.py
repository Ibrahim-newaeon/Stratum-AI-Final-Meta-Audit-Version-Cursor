"""Bootstrap the Paddle catalogue and webhook destination for Stratum AI (idempotent).

With nothing but ``PADDLE_API_KEY`` + ``PADDLE_ENVIRONMENT`` in the environment
the script creates or reuses:

- one Paddle product per subscription tier (matched by
  ``custom_data.stratum_tier``, fallback: exact name ``Stratum AI <Tier>``),
- one active recurring monthly USD price per product with exactly the
  catalogue amount (Starter 499, Professional 999; Enterprise is custom-priced,
  so its price is created only with ``--enterprise-usd <int>``). A tagged price
  whose amount no longer matches the catalogue is never reused: a new price is
  created and the stale one is reported so the operator can archive it,
- optionally (``--webhook-url``) the notification destination that delivers
  webhooks to ``POST /api/v1/webhooks/paddle``, subscribed to exactly the
  events ``app.api.v1.endpoints.paddle_webhook`` processes. An active ``url``
  destination with the same URL is reused; an inactive one or missing events
  produce a warning.

Run it through the Railway CLI so the api service's variables reach the
process and no secret is typed into a terminal or pasted into a chat::

    railway run -e staging --service api -- python scripts_paddle_bootstrap.py --dry-run
    railway run -e staging --service api -- python scripts_paddle_bootstrap.py \\
        --webhook-url https://<api-host>/api/v1/webhooks/paddle \\
        --railway-env staging --railway-service api

Without ``--railway-env/--railway-service`` the resulting ids are printed with
the ``railway variable set`` lines for the NON-secret values; with them the
price ids are written as ``railway variable set ... KEY=VALUE`` and, when the
destination was created in this run, the webhook secret is piped to
``railway variable set ... PADDLE_WEBHOOK_SECRET --stdin`` so it never appears
on a command line or in a process list. The Railway environment must sit on the
same side of the production boundary as the Paddle key (``--railway-env
production`` <-> ``PADDLE_ENVIRONMENT=production``); a sandbox run refuses to
write into the production service and vice versa. The webhook secret is never
printed unless ``--print-webhook-secret`` is given, and the API key is never
printed at all.

The script touches no database and no Redis: it imports only the settings,
the tier catalogue and the thin ``PaddleClient``.

Exit codes: 0 ok, 2 usage, 3 configuration (missing / placeholder / wrong-
environment API key, or a Railway/Paddle environment mismatch), 4 Paddle API
error, 5 Railway CLI error.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple, cast
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.core.tiers import TIER_HIERARCHY, TIER_PRICING, SubscriptionTier
from app.services.paddle_service import PaddleClient, PaddleError

# =============================================================================
# Constants
# =============================================================================

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_PADDLE = 4
EXIT_RAILWAY = 5

# Paddle API key prefixes (developer.paddle.com/api-reference/about/authentication):
# pdl_(live|sdbx)_apikey_<26>_<22>_<3>, 69 characters in total.
SANDBOX_KEY_PREFIX = "pdl_sdbx_apikey_"
PRODUCTION_KEY_PREFIX = "pdl_live_apikey_"
KEY_PREFIX_BY_ENVIRONMENT: dict[str, str] = {
    "sandbox": SANDBOX_KEY_PREFIX,
    "production": PRODUCTION_KEY_PREFIX,
}
PADDLE_PRODUCTION_ENVIRONMENT = "production"
# Railway's default production environment name; the boundary check compares against it.
RAILWAY_PRODUCTION_ENVIRONMENT = "production"
# Railway placeholders are literally "…" / "pri_…"; a real key is far longer.
PLACEHOLDER_MIN_LENGTH = 20
PLACEHOLDER_MARKERS = ("…", "...")

TIER_KEY = "stratum_tier"
PRODUCT_NAME_PREFIX = "Stratum AI"
PRODUCT_TAX_CATEGORY = "standard"
PRICE_CURRENCY = "USD"
BILLING_INTERVAL = "month"
BILLING_FREQUENCY = 1
QUANTITY_MINIMUM = 1
QUANTITY_MAXIMUM = 1

WEBHOOK_PATH = "/api/v1/webhooks/paddle"
WEBHOOK_DESCRIPTION = "Stratum AI - subscription, transaction and customer sync"
WEBHOOK_DESTINATION_TYPE = "url"
# Exactly the event types app.api.v1.endpoints.paddle_webhook handles
# (SUBSCRIPTION_SYNC_EVENTS + SUBSCRIPTION_CANCELED_EVENT + TRANSACTION_SYNC_EVENTS
# + TRANSACTION_FAILED_EVENT + CUSTOMER_EVENTS); asserted by the unit tests.
WEBHOOK_EVENTS: tuple[str, ...] = (
    "customer.created",
    "customer.updated",
    "subscription.activated",
    "subscription.canceled",
    "subscription.created",
    "subscription.past_due",
    "subscription.paused",
    "subscription.resumed",
    "subscription.trialing",
    "subscription.updated",
    "transaction.completed",
    "transaction.paid",
    "transaction.payment_failed",
)
WEBHOOK_INCLUDE_SENSITIVE_FIELDS = False
WEBHOOK_API_VERSION = 1

WEBHOOK_SECRET_ENV = "PADDLE_WEBHOOK_SECRET"
PRICE_ID_ENV: dict[SubscriptionTier, str] = {
    SubscriptionTier.STARTER: "PADDLE_STARTER_PRICE_ID",
    SubscriptionTier.PROFESSIONAL: "PADDLE_PROFESSIONAL_PRICE_ID",
    SubscriptionTier.ENTERPRISE: "PADDLE_ENTERPRISE_PRICE_ID",
}
REDACTED = "<redacted>"
PENDING = "<new>"

ENTERPRISE_NOTE = (
    "Enterprise: TIER_PRICING price is None (custom pricing), so no price was created and "
    "PADDLE_ENTERPRISE_PRICE_ID was left untouched. Pass --enterprise-usd <int> to create a monthly "
    "USD price, or create one in the Paddle dashboard and set the variable by hand.\n"
    "         While PADDLE_ENTERPRISE_PRICE_ID is empty: settings.paddle_fully_configured "
    "(app/core/config.py) is False - the property has no runtime callers, the API gates on "
    "paddle_service.is_configured() (= PADDLE_API_KEY set) plus the per-tier price id, so Starter and "
    "Professional checkout keep working; POST /api/v1/billing/checkout-session and "
    "POST /api/v1/billing/upgrade answer 400 for the enterprise tier; and with APP_ENV=production the "
    "settings validator refuses to start while PADDLE_API_KEY is set and PADDLE_ENTERPRISE_PRICE_ID is "
    "empty (outside production it only warns)."
)
SECRET_RECOVERY_HINT = (
    "copy it from the Paddle dashboard (Developer Tools > Notifications > this destination > "
    "Secret key) or re-run with --print-webhook-secret to display it once"
)


# =============================================================================
# Results
# =============================================================================


@dataclass
class TierResult:
    """Outcome for one subscription tier (product + price)."""

    tier: SubscriptionTier
    product_id: str | None = None
    product_created: bool = False
    price_id: str | None = None
    price_created: bool = False
    amount_minor: str | None = None
    # (price id, amount) of tagged prices that were NOT reused because their amount differs
    stale_prices: list[tuple[str, str]] = field(default_factory=list)
    skipped_reason: str | None = None

    @property
    def env_name(self) -> str:
        """Environment variable that carries this tier's price id."""
        return PRICE_ID_ENV[self.tier]


@dataclass
class WebhookResult:
    """Outcome for the notification destination."""

    destination: str
    setting_id: str | None
    created: bool
    secret: str | None
    active: bool = True
    missing_events: list[str] = field(default_factory=list)


@dataclass
class BootstrapReport:
    """Everything one run resolved (ids only; the secret lives in ``webhook.secret``)."""

    environment: str
    base_url: str
    dry_run: bool
    tiers: list[TierResult] = field(default_factory=list)
    webhook: WebhookResult | None = None
    warnings: list[str] = field(default_factory=list)

    def price_assignments(self) -> dict[str, str]:
        """``ENV_NAME -> price id`` for every tier that resolved a real price id."""
        return {
            result.env_name: result.price_id
            for result in self.tiers
            if result.price_id and result.price_id != PENDING
        }


# =============================================================================
# Guards
# =============================================================================


def is_placeholder_key(api_key: str | None) -> bool:
    """Return True for an empty key or a Railway placeholder (too short or containing an ellipsis)."""
    key = (api_key or "").strip()
    if len(key) < PLACEHOLDER_MIN_LENGTH:
        return True
    return any(marker in key for marker in PLACEHOLDER_MARKERS)


def check_key_environment(api_key: str, environment: str) -> tuple[str | None, str | None]:
    """
    Compare the API key prefix with ``PADDLE_ENVIRONMENT``.

    Returns ``(error, warning)``: an error when the key carries the *other*
    environment's prefix (a live key on a sandbox run or vice versa), a warning
    when the key matches neither documented prefix (its environment cannot be
    confirmed), ``(None, None)`` when the prefix matches.
    """
    expected = KEY_PREFIX_BY_ENVIRONMENT.get(environment)
    for env_name, prefix in KEY_PREFIX_BY_ENVIRONMENT.items():
        if api_key.startswith(prefix):
            if env_name == environment:
                return None, None
            error = (
                f"PADDLE_API_KEY is a {env_name} key ({prefix}...) but PADDLE_ENVIRONMENT="
                f"{environment}. Fix the variables before creating anything."
            )
            return error, None
    warning = (
        f"PADDLE_API_KEY does not start with {expected} (Paddle's documented {environment} "
        "prefix); the key's environment could not be confirmed."
    )
    return None, warning


def check_railway_environment(railway_env: str, paddle_environment: str) -> str | None:
    """
    Refuse to cross the production boundary between Railway and Paddle.

    Returns an error message when ``--railway-env production`` is combined with
    a non-production ``PADDLE_ENVIRONMENT`` (sandbox price ids and a sandbox
    webhook secret would land in the production service) or when a production
    Paddle run would write into a non-production Railway environment; ``None``
    when both sit on the same side of the boundary.
    """
    railway_is_production = railway_env.strip().lower() == RAILWAY_PRODUCTION_ENVIRONMENT
    paddle_is_production = paddle_environment == PADDLE_PRODUCTION_ENVIRONMENT
    if railway_is_production == paddle_is_production:
        return None
    return (
        f"--railway-env {railway_env} does not match PADDLE_ENVIRONMENT={paddle_environment}: "
        f"{paddle_environment} price ids and a {paddle_environment} webhook secret must not be written "
        f"into the '{railway_env}' Railway environment (checkout would fail on unknown price ids and "
        "every webhook would be rejected as a bad signature). Run the script with "
        f"`railway run -e {railway_env} --service <service>` so that environment's PADDLE_API_KEY and "
        "PADDLE_ENVIRONMENT are injected, or pass the matching --railway-env."
    )


# =============================================================================
# Catalogue helpers
# =============================================================================


def _tier_info(tier: SubscriptionTier) -> dict[str, Any]:
    """Return the ``TIER_PRICING`` entry of a tier with a precise type (the catalogue is untyped)."""
    return cast(dict[str, Any], TIER_PRICING[tier])


def product_name(tier: SubscriptionTier) -> str:
    """Return the Paddle product name for a tier (``Stratum AI Starter``)."""
    return f"{PRODUCT_NAME_PREFIX} {_tier_info(tier)['name']}"


def tier_amount_usd(tier: SubscriptionTier, enterprise_usd: int | None) -> int | None:
    """Return the monthly USD amount for a tier (Enterprise only from ``--enterprise-usd``)."""
    if tier == SubscriptionTier.ENTERPRISE:
        return enterprise_usd
    price = _tier_info(tier)["price"]
    return int(price) if price is not None else None


def usd_to_minor(amount_usd: int) -> str:
    """Convert whole US dollars to Paddle's minor-unit string (``499`` -> ``"49900"``)."""
    return str(int(amount_usd) * 100)


def _custom_tier(entity: dict[str, Any]) -> str | None:
    """Return ``custom_data.stratum_tier`` of a Paddle entity, if present."""
    custom = entity.get("custom_data")
    if not isinstance(custom, dict):
        return None
    value = custom.get(TIER_KEY)
    return str(value) if value else None


def find_product(products: Sequence[dict[str, Any]], tier: SubscriptionTier) -> dict[str, Any] | None:
    """Find the tier's product by ``custom_data.stratum_tier``, else by exact name."""
    for product in products:
        if _custom_tier(product) == tier.value:
            return product
    name = product_name(tier)
    for product in products:
        if product.get("name") == name:
            return product
    return None


def _is_monthly_usd(price: dict[str, Any]) -> bool:
    """Return True when a price bills every month in USD."""
    cycle = price.get("billing_cycle")
    unit = price.get("unit_price")
    if not isinstance(cycle, dict) or not isinstance(unit, dict):
        return False
    try:
        frequency = int(cycle.get("frequency") or 0)
    except (TypeError, ValueError):
        return False
    return (
        cycle.get("interval") == BILLING_INTERVAL
        and frequency == BILLING_FREQUENCY
        and unit.get("currency_code") == PRICE_CURRENCY
    )


def _price_amount(price: dict[str, Any]) -> str | None:
    """Return ``unit_price.amount`` of a price as a string (minor units), if present."""
    unit = price.get("unit_price")
    if not isinstance(unit, dict) or unit.get("amount") is None:
        return None
    return str(unit.get("amount"))


class PriceMatch(NamedTuple):
    """Result of ``find_price``: the reusable price (if any) and stale tagged prices."""

    price: dict[str, Any] | None
    stale: list[tuple[str, str]]


def find_price(
    prices: Sequence[dict[str, Any]],
    tier: SubscriptionTier,
    product_id: str,
    amount_minor: str,
) -> PriceMatch:
    """
    Find the tier's monthly USD price on ``product_id`` with exactly ``amount_minor``.

    Primary key: ``custom_data.stratum_tier`` + product + month/1 + USD + amount.
    Fallback (prices created by hand in the dashboard): an untagged price on the
    same product, month/1, USD and the same amount. A tagged price with another
    amount is never reused (the catalogue changed); its id and amount are
    returned in ``stale`` so the operator can archive it.
    """
    candidates = [
        price
        for price in prices
        if price.get("product_id") == product_id and _is_monthly_usd(price)
    ]
    match: dict[str, Any] | None = None
    stale: list[tuple[str, str]] = []
    for price in candidates:
        if _custom_tier(price) != tier.value:
            continue
        actual = _price_amount(price)
        if actual == amount_minor:
            if match is None:
                match = price
        else:
            stale.append((str(price.get("id")), actual or "?"))
    if match is None:
        for price in candidates:
            if _custom_tier(price) is None and _price_amount(price) == amount_minor:
                match = price
                break
    return PriceMatch(price=match, stale=stale)


def _event_names(subscribed: Any) -> set[str]:
    """
    Return the event names of a destination's ``subscribed_events``.

    Paddle returns objects (``{"name": "transaction.paid", "description": ...,
    "group": ..., "available_versions": [...]}``) on create/get/list; plain
    strings (the request shape) are tolerated as well.
    """
    if not isinstance(subscribed, list):
        return set()
    names: set[str] = set()
    for item in subscribed:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                names.add(str(name))
        elif item:
            names.add(str(item))
    return names


# =============================================================================
# Bootstrap
# =============================================================================


def build_client() -> PaddleClient:
    """Create the Paddle client from settings (tests replace this with a mocked transport)."""
    return PaddleClient()


async def ensure_product(
    client: PaddleClient,
    result: TierResult,
    products: Sequence[dict[str, Any]],
    dry_run: bool,
) -> None:
    """Fill ``result.product_id`` / ``product_created`` (the id stays None when a dry run would create it)."""
    existing = find_product(products, result.tier)
    if existing is not None:
        result.product_id = str(existing.get("id"))
        result.product_created = False
        return
    result.product_created = True
    if dry_run:
        return
    created = await client.create_product(
        name=product_name(result.tier),
        tax_category=PRODUCT_TAX_CATEGORY,
        description=str(_tier_info(result.tier)["description"]),
        custom_data={TIER_KEY: result.tier.value},
    )
    result.product_id = str(created.get("id"))


async def ensure_price(
    client: PaddleClient,
    result: TierResult,
    amount_minor: str,
    dry_run: bool,
) -> None:
    """
    Fill ``result.price_id`` / ``price_created`` / ``amount_minor`` / ``stale_prices``.

    Only a price with exactly the catalogue amount is reused (its own amount is
    what ``result.amount_minor`` then reports); tagged prices with another
    amount are recorded in ``stale_prices`` and a new price is created. The id
    stays None when a dry run would create it.
    """
    result.amount_minor = amount_minor
    if result.product_id is not None:
        prices = await client.list_prices(product_id=result.product_id)
        match = find_price(prices, result.tier, result.product_id, amount_minor)
        result.stale_prices = match.stale
        if match.price is not None:
            result.price_id = str(match.price.get("id"))
            result.price_created = False
            result.amount_minor = _price_amount(match.price) or amount_minor
            return
    result.price_created = True
    if dry_run or result.product_id is None:
        return
    created = await client.create_price(
        product_id=result.product_id,
        description=f"{_tier_info(result.tier)['name']} monthly",
        amount_minor=amount_minor,
        currency_code=PRICE_CURRENCY,
        billing_interval=BILLING_INTERVAL,
        billing_frequency=BILLING_FREQUENCY,
        quantity_minimum=QUANTITY_MINIMUM,
        quantity_maximum=QUANTITY_MAXIMUM,
        custom_data={TIER_KEY: result.tier.value},
    )
    result.price_id = str(created.get("id"))


async def ensure_webhook(client: PaddleClient, destination: str, dry_run: bool) -> WebhookResult:
    """Reuse the ``url`` notification destination with this URL or create it (never logs the secret)."""
    for setting in await client.list_notification_settings():
        if setting.get("type") != WEBHOOK_DESTINATION_TYPE or setting.get("destination") != destination:
            continue
        subscribed_names = _event_names(setting.get("subscribed_events"))
        missing = [event for event in WEBHOOK_EVENTS if event not in subscribed_names]
        secret = setting.get("endpoint_secret_key")
        return WebhookResult(
            destination=destination,
            setting_id=str(setting.get("id")) if setting.get("id") else None,
            created=False,
            secret=str(secret) if secret else None,
            active=setting.get("active") is not False,
            missing_events=missing,
        )
    if dry_run:
        return WebhookResult(destination=destination, setting_id=None, created=True, secret=None)
    created = await client.create_notification_setting(
        description=WEBHOOK_DESCRIPTION,
        destination=destination,
        subscribed_events=list(WEBHOOK_EVENTS),
        destination_type=WEBHOOK_DESTINATION_TYPE,
        include_sensitive_fields=WEBHOOK_INCLUDE_SENSITIVE_FIELDS,
        api_version=WEBHOOK_API_VERSION,
    )
    secret = created.get("endpoint_secret_key")
    return WebhookResult(
        destination=destination,
        setting_id=str(created.get("id")) if created.get("id") else None,
        created=True,
        secret=str(secret) if secret else None,
        active=created.get("active") is not False,
    )


async def bootstrap(
    client: PaddleClient,
    enterprise_usd: int | None,
    webhook_url: str | None,
    dry_run: bool,
) -> BootstrapReport:
    """Resolve products, prices and (optionally) the webhook destination; closes the client."""
    report = BootstrapReport(environment=client.environment, base_url=client.base_url, dry_run=dry_run)
    try:
        products = await client.list_products(status="active")
        for tier in TIER_HIERARCHY:
            result = TierResult(tier=tier)
            await ensure_product(client, result, products, dry_run)
            amount_usd = tier_amount_usd(tier, enterprise_usd)
            if amount_usd is None:
                result.skipped_reason = "custom pricing (pass --enterprise-usd <int>)"
            else:
                await ensure_price(client, result, usd_to_minor(amount_usd), dry_run)
                for stale_id, stale_amount in result.stale_prices:
                    report.warnings.append(
                        f"{tier.value} price {stale_id} carries the {TIER_KEY} tag but its amount is "
                        f"{stale_amount}, not the catalogue's {result.amount_minor}; it was not reused and "
                        f"a price with the catalogue amount {'would be' if dry_run else 'was'} created. "
                        f"Archive {stale_id} in the Paddle dashboard (Catalog > Products) once no "
                        "subscription uses it, or restore TIER_PRICING / --enterprise-usd."
                    )
            report.tiers.append(result)

        if webhook_url:
            report.webhook = await ensure_webhook(client, webhook_url, dry_run)
            if not report.webhook.active:
                report.warnings.append(
                    f"destination {report.webhook.setting_id} is inactive (active=false): Paddle delivers "
                    "nothing to it. Re-activate it in the Paddle dashboard (Developer Tools > Notifications), "
                    "or delete it there and re-run to create an active one."
                )
            if report.webhook.missing_events:
                report.warnings.append(
                    f"destination {report.webhook.setting_id} is not subscribed to "
                    f"{', '.join(report.webhook.missing_events)}; add them in the Paddle dashboard "
                    "(Developer Tools > Notifications) or the handler will never see those events."
                )
    finally:
        await client.aclose()
    return report


# =============================================================================
# Railway CLI
# =============================================================================


class RailwayError(RuntimeError):
    """``railway variable set`` failed; ``written`` lists the keys stored before the failure."""

    def __init__(self, message: str, written: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.written: list[str] = list(written)


def railway_argv(env: str, service: str, assignments: dict[str, str]) -> list[str]:
    """Build the ``railway variable set`` argv (no shell) for ``KEY=VALUE`` pairs."""
    return [
        "railway",
        "variable",
        "set",
        "-e",
        env,
        "--service",
        service,
        "--skip-deploys",
        *[f"{key}={value}" for key, value in assignments.items()],
    ]


def railway_stdin_argv(env: str, service: str, key: str) -> list[str]:
    """Build the ``railway variable set ... KEY --stdin`` argv; the value is piped, never an argument."""
    return [
        "railway",
        "variable",
        "set",
        "-e",
        env,
        "--service",
        service,
        "--skip-deploys",
        key,
        "--stdin",
    ]


def redact(text: str, secrets: Sequence[str]) -> str:
    """Replace every secret value in ``text`` with ``<redacted>``."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def _run_railway(argv: list[str], secrets: Sequence[str], stdin_value: str | None = None) -> None:
    """
    Run one ``railway`` command (argv list, no shell), optionally piping ``stdin_value``.

    Raises:
        RuntimeError: when the CLI is missing or exits non-zero (output redacted).
    """
    kwargs: dict[str, Any] = {"capture_output": True, "text": True}
    if stdin_value is not None:
        kwargs["input"] = stdin_value
    try:
        completed = subprocess.run(argv, check=False, **kwargs)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "railway CLI not found on PATH (install it or drop --railway-env/--railway-service "
            "and set the variables by hand)"
        ) from exc
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            f"railway variable set exited with {completed.returncode}: {redact(output, secrets)}"
        )


def write_railway_variables(
    env: str,
    service: str,
    assignments: dict[str, str],
    secret_key: str | None = None,
    secret_value: str | None = None,
) -> list[str]:
    """
    Write ``assignments`` as ``KEY=VALUE`` arguments, then ``secret_value`` through ``--stdin``.

    The secret never becomes a command-line argument (it would be visible in the
    process list); Railway reads it from stdin and strips trailing newlines.

    Returns:
        The keys written, in order.

    Raises:
        RailwayError: when the CLI is missing or exits non-zero; ``written``
        carries the keys already stored before the failure.
    """
    secrets = [secret_value] if secret_value else []
    written: list[str] = []
    if assignments:
        try:
            _run_railway(railway_argv(env, service, assignments), secrets)
        except RuntimeError as exc:
            raise RailwayError(f"{exc} (while writing {', '.join(assignments)})", written) from exc
        written.extend(assignments)
    if secret_key and secret_value:
        try:
            _run_railway(railway_stdin_argv(env, service, secret_key), secrets, stdin_value=secret_value)
        except RuntimeError as exc:
            raise RailwayError(f"{exc} (while piping {secret_key} through --stdin)", written) from exc
        written.append(secret_key)
    return written


# =============================================================================
# Output
# =============================================================================


def _describe(entity_id: str | None, created: bool) -> str:
    """Render ``<id> created|reused`` (or ``<new> would create`` on a dry run)."""
    if entity_id is None:
        return f"{PENDING} would create"
    return f"{entity_id} {'created' if created else 'reused'}"


def _env_state(current: str | None, resolved: str | None) -> str:
    """Compare the process environment's price id with the resolved one."""
    if resolved is None:
        return "pending"
    if not current:
        return f"unset -> {resolved}"
    if is_placeholder_key(current):
        return f"placeholder -> {resolved}"
    if current == resolved:
        return "matches"
    return f"differs (currently {current}) -> {resolved}"


def render_report(
    report: BootstrapReport,
    railway_env: str | None,
    railway_service: str | None,
    print_secret: bool,
) -> str:
    """Render the human-readable summary; contains the secret only with ``print_secret``."""
    mode = "  [DRY RUN - nothing was created]" if report.dry_run else ""
    lines = [f"Paddle bootstrap - environment: {report.environment} ({report.base_url}){mode}", ""]

    lines.append(f"{'tier':<14}{'product':<44}price")
    for result in report.tiers:
        product = _describe(result.product_id, result.product_created)
        if result.skipped_reason:
            price = f"skipped: {result.skipped_reason}"
        else:
            price = _describe(result.price_id, result.price_created)
            if result.amount_minor:
                price += f"  {result.amount_minor} {PRICE_CURRENCY} minor units / {BILLING_INTERVAL}"
        lines.append(f"{result.tier.value:<14}{product:<44}{price}")
    lines.append("")

    lines.append("Webhook destination")
    if report.webhook is None:
        lines.append("  not requested (pass --webhook-url https://<api-host>" + WEBHOOK_PATH + ")")
    else:
        state = "" if report.webhook.active else "  [INACTIVE - see warning]"
        lines.append(
            f"  {_describe(report.webhook.setting_id, report.webhook.created)}  "
            f"{report.webhook.destination}{state}"
        )
    lines.append("")

    lines.append("Process environment vs catalogue")
    current_values = {
        SubscriptionTier.STARTER: settings.paddle_starter_price_id,
        SubscriptionTier.PROFESSIONAL: settings.paddle_professional_price_id,
        SubscriptionTier.ENTERPRISE: settings.paddle_enterprise_price_id,
    }
    for result in report.tiers:
        state = "untouched (see note)" if result.skipped_reason else _env_state(
            current_values[result.tier], result.price_id
        )
        lines.append(f"  {result.env_name:<30}{state}")
    lines.append("")

    assignments = report.price_assignments()
    env_arg = railway_env or "<environment>"
    service_arg = railway_service or "<service>"
    if assignments:
        lines.append("Railway variables (non-secret values only):")
        lines.append("  " + " ".join(railway_argv(env_arg, service_arg, assignments)))
    else:
        lines.append("Railway variables: no price id resolved yet (see the table above).")

    if report.webhook is not None:
        if report.webhook.created and report.webhook.secret:
            if railway_env and railway_service:
                lines.append(
                    f"  {WEBHOOK_SECRET_ENV}: will be piped to `railway variable set {WEBHOOK_SECRET_ENV} "
                    "--stdin` by this script (never printed, never a command-line argument); the result "
                    "is reported below."
                )
            else:
                lines.append(
                    f"  {WEBHOOK_SECRET_ENV}: NOT printed. Copy the destination's secret key from "
                    "the Paddle dashboard (Developer Tools > Notifications > this destination), or "
                    "re-run with --railway-env/--railway-service to have it written into Railway, "
                    "or with --print-webhook-secret to display it once."
                )
        elif report.webhook.created:
            lines.append(f"  {WEBHOOK_SECRET_ENV}: would be created with the destination (dry run).")
        else:
            lines.append(
                f"  {WEBHOOK_SECRET_ENV}: destination already existed, so nothing was written. "
                "Paddle returns endpoint_secret_key on GET /notification-settings: re-run with "
                "--print-webhook-secret to display it once, or copy it from the Paddle dashboard "
                "(Developer Tools > Notifications > this destination)."
            )
    lines.append("")

    for tier_result in report.tiers:
        if tier_result.tier == SubscriptionTier.ENTERPRISE and tier_result.skipped_reason:
            lines.append(f"note     {ENTERPRISE_NOTE}")
            lines.append("")
    for warning in report.warnings:
        lines.append(f"warning  {warning}")
    if report.warnings:
        lines.append("")

    if print_secret and report.webhook is not None and report.webhook.secret:
        lines.append("=" * 78)
        lines.append(
            "WARNING  printing the webhook endpoint secret because --print-webhook-secret was given."
        )
        lines.append(
            "         Anyone who can read this terminal, its scrollback or a CI log can forge webhooks."
        )
        lines.append("         Do not paste it into a chat or a ticket; rotate it in Paddle if it leaks.")
        lines.append(f"{WEBHOOK_SECRET_ENV}={report.webhook.secret}")
        lines.append("=" * 78)
        lines.append("")
    elif print_secret and report.webhook is not None:
        lines.append(
            "warning  --print-webhook-secret given but no secret is available "
            "(dry run, or Paddle returned none)."
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# =============================================================================
# CLI
# =============================================================================


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="scripts_paddle_bootstrap.py",
        description=(
            "Create or reuse the Stratum AI products, monthly prices and webhook destination in "
            "Paddle (idempotent). Reads PADDLE_API_KEY / PADDLE_ENVIRONMENT from the environment; "
            "run it with `railway run -e <env> --service api -- python scripts_paddle_bootstrap.py`."
        ),
        epilog=(
            "Exit codes: 0 ok, 2 usage, 3 configuration (missing / placeholder / wrong-environment "
            "API key, or --railway-env on the other side of the production boundary than "
            "PADDLE_ENVIRONMENT), 4 Paddle API error, 5 Railway CLI error."
        ),
    )
    parser.add_argument(
        "--enterprise-usd",
        type=int,
        default=None,
        metavar="USD",
        help=(
            "Monthly USD amount for the Enterprise price (whole dollars). Without it the "
            "Enterprise price is skipped and PADDLE_ENTERPRISE_PRICE_ID is left untouched."
        ),
    )
    parser.add_argument(
        "--webhook-url",
        default=None,
        metavar="URL",
        help=f"Create/reuse the notification destination for https://<api-host>{WEBHOOK_PATH}.",
    )
    parser.add_argument(
        "--railway-env",
        default=None,
        metavar="ENV",
        help=(
            "Railway environment to write the variables into (requires --railway-service; "
            "'production' only with PADDLE_ENVIRONMENT=production and vice versa)."
        ),
    )
    parser.add_argument(
        "--railway-service",
        default=None,
        metavar="SERVICE",
        help="Railway service to write the variables into (requires --railway-env).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list what exists and print what would be created/written; no POST, no CLI writes.",
    )
    parser.add_argument(
        "--print-webhook-secret",
        action="store_true",
        help="Print the webhook endpoint secret once (loud warning). Off by default.",
    )
    return parser


def _usage_error(parser: argparse.ArgumentParser, message: str) -> int:
    """Print a usage error to stderr and return the usage exit code."""
    print(f"ERROR    {message}", file=sys.stderr)
    print(f"         {parser.format_usage().strip()}", file=sys.stderr)
    return EXIT_USAGE


def _print_railway_dry_run(report: BootstrapReport, env: str, service: str) -> None:
    """Print the ``railway variable set`` commands a real run would execute (ids pending, secret redacted)."""
    preview = {
        result.env_name: result.price_id or PENDING
        for result in report.tiers
        if not result.skipped_reason
    }
    lines: list[str] = []
    if preview:
        lines.append("  " + " ".join(railway_argv(env, service, preview)))
    if report.webhook is not None and report.webhook.created:
        lines.append(
            "  "
            + " ".join(railway_stdin_argv(env, service, WEBHOOK_SECRET_ENV))
            + f"   (value piped through stdin: {REDACTED})"
        )
    if not lines:
        print("Railway (dry run): nothing would be written.")
        return
    print("Railway (dry run) would run:")
    for line in lines:
        print(line)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the bootstrap; return the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if bool(args.railway_env) != bool(args.railway_service):
        return _usage_error(parser, "--railway-env and --railway-service must be given together")
    if args.enterprise_usd is not None and args.enterprise_usd <= 0:
        return _usage_error(parser, "--enterprise-usd must be a positive whole-dollar amount")
    warnings: list[str] = []
    if args.webhook_url:
        parts = urlsplit(args.webhook_url)
        if parts.scheme != "https" or not parts.netloc:
            return _usage_error(parser, "--webhook-url must be an https:// URL")
        if parts.path.rstrip("/") != WEBHOOK_PATH:
            warnings.append(
                f"--webhook-url path is {parts.path or '/'}; the API serves the webhook at "
                f"{WEBHOOK_PATH}."
            )

    api_key = settings.paddle_api_key or ""
    environment = settings.paddle_environment
    if is_placeholder_key(api_key):
        print(
            "ERROR    PADDLE_API_KEY is empty or a placeholder. Create an API key in the Paddle "
            "dashboard (Developer Tools > Authentication > API keys), store it in the Railway "
            "service variables and run this script through `railway run` so it is injected.",
            file=sys.stderr,
        )
        return EXIT_CONFIG
    error, warning = check_key_environment(api_key, environment)
    if error:
        print(f"ERROR    {error}", file=sys.stderr)
        return EXIT_CONFIG
    if warning:
        warnings.append(warning)
    if args.railway_env:
        mismatch = check_railway_environment(args.railway_env, environment)
        if mismatch:
            print(f"ERROR    {mismatch}", file=sys.stderr)
            return EXIT_CONFIG

    client = build_client()
    try:
        report = asyncio.run(
            bootstrap(
                client,
                enterprise_usd=args.enterprise_usd,
                webhook_url=args.webhook_url,
                dry_run=args.dry_run,
            )
        )
    except PaddleError as exc:
        print(f"ERROR    Paddle API ({environment}): {exc}", file=sys.stderr)
        return EXIT_PADDLE
    except httpx.HTTPError as exc:  # pragma: no cover - PaddleClient wraps transport errors
        print(f"ERROR    Paddle API unreachable: {exc}", file=sys.stderr)
        return EXIT_PADDLE
    report.warnings = warnings + report.warnings

    print(
        render_report(
            report,
            railway_env=args.railway_env,
            railway_service=args.railway_service,
            print_secret=args.print_webhook_secret,
        ),
        end="",
    )

    if not (args.railway_env and args.railway_service):
        return EXIT_OK

    if args.dry_run:
        _print_railway_dry_run(report, args.railway_env, args.railway_service)
        return EXIT_OK

    assignments = report.price_assignments()
    secret_value: str | None = None
    if report.webhook is not None and report.webhook.created and report.webhook.secret:
        secret_value = report.webhook.secret
    if not assignments and not secret_value:
        print("Railway: nothing to write.")
        return EXIT_OK

    try:
        written = write_railway_variables(
            args.railway_env,
            args.railway_service,
            assignments,
            secret_key=WEBHOOK_SECRET_ENV if secret_value else None,
            secret_value=secret_value,
        )
    except RailwayError as exc:
        print(f"ERROR    {exc}", file=sys.stderr)
        if exc.written:
            print(
                f"Railway: {', '.join(exc.written)} were written to service '{args.railway_service}' "
                f"in environment '{args.railway_env}' before the failure."
            )
        if secret_value and WEBHOOK_SECRET_ENV not in exc.written:
            print(
                f"Railway: {WEBHOOK_SECRET_ENV} was NOT written. The destination now exists, so a "
                f"re-run will not write it either: {SECRET_RECOVERY_HINT}."
            )
        return EXIT_RAILWAY
    print(
        f"Railway: wrote {', '.join(written)} to service '{args.railway_service}' in "
        f"environment '{args.railway_env}' (--skip-deploys"
        f"{'; the secret was piped through --stdin' if secret_value else ''})."
    )
    print(
        f"         Redeploy the service so it loads them: "
        f"railway redeploy -e {args.railway_env} --service {args.railway_service}"
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
