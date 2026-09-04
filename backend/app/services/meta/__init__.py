# =============================================================================
# Stratum AI - Meta Marketing API Services (read-only)
# =============================================================================
"""
Read-only Meta Marketing API integration.

- ``insights_client``: thin ``httpx.AsyncClient`` over
  ``GET /{version}/act_<id>/insights`` (Ads Insights API). GET only - this
  package never creates, updates, pauses or otherwise mutates anything on
  Meta. The permission it needs is ``ads_read``.
- ``insights_ingestion``: maps insight rows onto ``CampaignMetric`` and
  upserts them by ``(campaign_id, date)``.

Autopilot *writes* are a separate, deliberately unwired path
(``app/tasks/apply_actions_queue.py``); nothing here touches it.
"""

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

__all__ = [
    "GRAPH_API_BASE_URL",
    "INSIGHTS_FIELDS",
    "RATE_LIMIT_ERROR_CODES",
    "TOKEN_ERROR_CODES",
    "VIDEO_VIEW_ACTION_TYPE",
    "ZERO_DECIMAL_CURRENCIES",
    "MetaAPIError",
    "MetaCredentials",
    "MetaCredentialsError",
    "MetaIngestionResult",
    "MetaInsightRow",
    "MetaInsightsClient",
    "MetaInsightsTruncatedError",
    "MetaRateLimitError",
    "MetaTokenError",
    "fetch_campaign_insight_rows",
    "first_present_action_type",
    "graph_api_version",
    "ingest_campaign_insights",
    "metric_values_from_row",
    "resolve_meta_credentials",
    "sum_action_values",
    "sum_one_action_type",
    "to_hundredths",
    "upsert_campaign_metric",
]
