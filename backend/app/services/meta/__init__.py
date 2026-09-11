# =============================================================================
# Stratum AI - Meta Marketing API Services
# =============================================================================
"""
Meta Marketing API integration, split by direction so the read path cannot
acquire a write by accident.

**Read (``ads_read``)**

- ``insights_client``: thin ``httpx.AsyncClient`` over
  ``GET /{version}/act_<id>/insights`` (Ads Insights API). GET only - this
  module never creates, updates, pauses or otherwise mutates anything.
- ``insights_ingestion``: maps insight rows onto ``CampaignMetric`` and
  upserts them by ``(campaign_id, date)``.
- ``campaign_discovery_client``: thin ``httpx.AsyncClient`` over
  ``GET /{version}/act_<id>/campaigns``. GET only - lists campaign
  metadata so local ``Campaign`` rows can be upserted.
- ``campaign_discovery``: maps discovered campaigns onto the unified
  ``Campaign`` model by ``(tenant_id, platform, external_id)``. Does not
  advance ``last_synced_at`` (insights owns freshness).

**Write (``ads_management``, off by default)**

- ``write_client``: the only module that can change something on Meta. Reads
  an entity's current state and applies narrowly allowlisted updates to a
  campaign, ad set or ad. It is also the single documented place where an
  amount in the account's major unit becomes Meta's API units - a conversion
  that differs from this schema's ``*_cents`` convention by 100x for a
  zero-decimal currency such as JPY.
- ``action_executor``: drives ``fact_actions_queue`` rows through the trust
  gate, the tenant enforcement mode and the guard rails, measures the before-
  and after-values, and provides the one-click revert.

Nothing on the write path runs unless ``autopilot_execution_enabled`` is true,
and nothing is written unless ``autopilot_execution_dry_run`` is also false.
The Celery task that drives it is scheduled every 5 minutes, which is not the
same as enabled: with the shipped defaults each run refuses every row with
``EXECUTION_DISABLED`` before a token is decrypted. See
docs/architecture/trust-engine.md.
"""

from app.services.meta.campaign_discovery import (
    CampaignDiscoveryResult,
    discover_tenant_campaigns,
    map_meta_campaign_status,
    resolve_meta_read_connection,
)
from app.services.meta.campaign_discovery_client import (
    CAMPAIGN_DISCOVERY_FIELDS,
    MetaCampaignDiscoveryClient,
    MetaCampaignRow,
    MetaCampaignsTruncatedError,
)
from app.services.meta.insights_client import (
    GRAPH_API_BASE_URL,
    INSIGHTS_FIELDS,
    RATE_LIMIT_ERROR_CODES,
    TOKEN_ERROR_CODES,
    VIDEO_VIEW_ACTION_TYPE,
    MetaAPIError,
    MetaInsightRow,
    MetaInsightsClient,
    MetaInsightsTruncatedError,
    MetaRateLimitError,
    MetaTokenError,
    first_present_action_type,
    graph_api_version,
    sum_action_values,
    sum_one_action_type,
)
from app.services.meta.insights_ingestion import (
    ZERO_DECIMAL_CURRENCIES,
    MetaCredentials,
    MetaCredentialsError,
    MetaIngestionResult,
    fetch_campaign_insight_rows,
    ingest_campaign_insights,
    metric_values_from_row,
    resolve_meta_credentials,
    to_hundredths,
    upsert_campaign_metric,
)
from app.services.meta.write_client import (
    META_CURRENCY_OFFSET,
    META_ZERO_DECIMAL_CURRENCIES,
    WRITABLE_FIELDS,
    WRITABLE_STATUSES,
    MetaEntityType,
    MetaWriteAmbiguousError,
    MetaWriteClient,
    MetaWriteValidationError,
    UnsupportedCurrencyError,
    hundredths_to_meta_minor,
    major_to_meta_minor,
    meta_currency_offset,
    meta_minor_to_major,
)

__all__ = [
    "CAMPAIGN_DISCOVERY_FIELDS",
    "GRAPH_API_BASE_URL",
    "INSIGHTS_FIELDS",
    "META_CURRENCY_OFFSET",
    "META_ZERO_DECIMAL_CURRENCIES",
    "RATE_LIMIT_ERROR_CODES",
    "TOKEN_ERROR_CODES",
    "VIDEO_VIEW_ACTION_TYPE",
    "WRITABLE_FIELDS",
    "WRITABLE_STATUSES",
    "ZERO_DECIMAL_CURRENCIES",
    "CampaignDiscoveryResult",
    "MetaAPIError",
    "MetaCampaignDiscoveryClient",
    "MetaCampaignRow",
    "MetaCampaignsTruncatedError",
    "MetaCredentials",
    "MetaCredentialsError",
    "MetaEntityType",
    "MetaIngestionResult",
    "MetaInsightRow",
    "MetaInsightsClient",
    "MetaInsightsTruncatedError",
    "MetaRateLimitError",
    "MetaTokenError",
    "MetaWriteAmbiguousError",
    "MetaWriteClient",
    "MetaWriteValidationError",
    "UnsupportedCurrencyError",
    "discover_tenant_campaigns",
    "fetch_campaign_insight_rows",
    "first_present_action_type",
    "graph_api_version",
    "hundredths_to_meta_minor",
    "ingest_campaign_insights",
    "major_to_meta_minor",
    "map_meta_campaign_status",
    "meta_currency_offset",
    "meta_minor_to_major",
    "metric_values_from_row",
    "resolve_meta_credentials",
    "resolve_meta_read_connection",
    "sum_action_values",
    "sum_one_action_type",
    "to_hundredths",
    "upsert_campaign_metric",
]
