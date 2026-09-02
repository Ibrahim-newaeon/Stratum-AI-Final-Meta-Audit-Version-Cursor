# =============================================================================
# Stratum AI - Measurement & Verification Services
# =============================================================================
"""
Measurement-only integrations (never ad channels):

- ``ga4_client``: read-only GA4 Data API client (service account,
  scope ``https://www.googleapis.com/auth/analytics.readonly``).
- ``ga4_ingestion``: daily baseline ingestion into ``fact_ga4_daily`` with
  Meta-traffic classification, plus baseline aggregation helpers.
- ``gtm_service``: Google Tag Manager helpers (container validation,
  web/server-side snippets, CDP 'sgtm' source linkage, verification).

Stratum AI only reads GA4 reports and only deploys tags through GTM; it never
reads or acts on non-Meta ad campaigns.
"""

from app.services.measurement.ga4_client import (
    GA4_READONLY_SCOPE,
    GA4AuthError,
    GA4ClientError,
    GA4DailyRow,
    GA4DataClient,
    GA4NotConfiguredError,
    GA4TestResult,
)
from app.services.measurement.ga4_ingestion import (
    GA4BaselineSummary,
    GA4SyncResult,
    classify_meta_traffic,
    get_ga4_baseline,
    list_tenants_with_ga4,
    load_ga4_client_for_tenant,
    sync_ga4_for_tenant,
)
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

__all__ = [
    "GA4_READONLY_SCOPE",
    "GTM_CONTAINER_ID_RE",
    "GA4AuthError",
    "GA4BaselineSummary",
    "GA4ClientError",
    "GA4DailyRow",
    "GA4DataClient",
    "GA4NotConfiguredError",
    "GA4SyncResult",
    "GA4TestResult",
    "GTMSnippets",
    "GTMVerifyResult",
    "build_snippets",
    "classify_meta_traffic",
    "ensure_sgtm_source",
    "get_ga4_baseline",
    "list_tenants_with_ga4",
    "load_ga4_client_for_tenant",
    "sync_ga4_for_tenant",
    "validate_container_id",
    "validate_server_container_url",
    "verify_containers",
]
