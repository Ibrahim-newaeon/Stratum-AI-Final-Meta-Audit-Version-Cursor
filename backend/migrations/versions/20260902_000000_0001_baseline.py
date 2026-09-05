"""Baseline schema.

Revision ID: 0001_baseline
Revises: None
Create Date: 2026-09-02

The migration history that preceded this baseline was incomplete (revisions
referenced files that no longer exist), so the schema is re-based here on the
SQLAlchemy models as of this revision. Upgrading an empty database creates every
table from the model metadata; databases that were created directly from the
models before this file existed should be stamped at this revision instead
(scripts_db_prepare.py does that automatically).

Every migration after this one must be a normal Alembic diff.
"""

from collections import Counter

from alembic import op

import app.models  # noqa: F401  (registers every mapper on Base)
from app.db.base import Base

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


# The tables this revision is responsible for, frozen as of 2026-09-02.
#
# This list used to be implicit: upgrade() called create_all() over whatever
# Base.metadata happened to hold. That made the baseline grow every time a
# model was added, so it would create tables belonging to *later* revisions and
# those revisions then failed on a fresh database with "relation already
# exists". A baseline has to be a fixed snapshot, so the set is pinned here.
# Do not add to it; new tables belong in their own revision.
BASELINE_TABLES = frozenset({
    "api_keys",
    "attribution_snapshots",
    "audience_sync_credentials",
    "audience_sync_jobs",
    "audit_logs",
    "campaign_draft",
    "campaign_metrics",
    "campaign_publish_log",
    "campaigns",
    "cdp_canonical_identities",
    "cdp_computed_traits",
    "cdp_consents",
    "cdp_events",
    "cdp_funnel_entries",
    "cdp_funnels",
    "cdp_identity_links",
    "cdp_profile_identifiers",
    "cdp_profile_merges",
    "cdp_profiles",
    "cdp_segment_memberships",
    "cdp_segments",
    "cdp_sources",
    "cdp_webhooks",
    "changelog_entries",
    "changelog_read_status",
    "channel_interactions",
    "client_assignments",
    "client_requests",
    "clients",
    "cms_authors",
    "cms_categories",
    "cms_contact_submissions",
    "cms_pages",
    "cms_post_tags",
    "cms_post_versions",
    "cms_posts",
    "cms_tags",
    "cms_workflow_logs",
    "cogs_uploads",
    "competitor_benchmarks",
    "conversion_paths",
    "creative_assets",
    "crm_connections",
    "crm_contacts",
    "crm_deals",
    "crm_sync_logs",
    "crm_writeback_configs",
    "crm_writeback_syncs",
    "daily_attributed_revenue",
    "daily_kpis",
    "daily_pipeline_metrics",
    "daily_profit_metrics",
    "delivery_channel_configs",
    "enforcement_audit_logs",
    "fact_actions_queue",
    "fact_attribution_variance_daily",
    "fact_ga4_daily",
    "fact_signal_health_daily",
    "forecasts",
    "landing_page_section_contents",
    "landing_page_sections",
    "landing_page_subscribers",
    "landing_page_templates",
    "landing_page_translations",
    "landing_pages",
    "margin_rules",
    "ml_predictions",
    "model_training_runs",
    "notification_preferences",
    "notifications",
    "pacing_alerts",
    "pacing_summaries",
    "paddle_webhook_events",
    "pending_confirmation_tokens",
    "platform_audiences",
    "product_catalog",
    "product_margins",
    "profit_roas_reports",
    "report_deliveries",
    "report_executions",
    "report_templates",
    "rule_executions",
    "rules",
    "scheduled_reports",
    "signal_health_history",
    "slack_integrations",
    "targets",
    "tenant_ad_account",
    "tenant_enforcement_rules",
    "tenant_enforcement_settings",
    "tenant_ga4_integrations",
    "tenant_gtm_integrations",
    "tenant_onboarding",
    "tenant_platform_connection",
    "tenants",
    "touchpoints",
    "trained_attribution_models",
    "trust_gate_audit_log",
    "users",
    "webhook_deliveries",
    "webhooks",
    "whatsapp_contacts",
    "whatsapp_conversations",
    "whatsapp_messages",
    "whatsapp_templates",
})


def _baseline_tables():
    """The subset of Base.metadata that this revision owns."""
    return [
        Base.metadata.tables[name]
        for name in sorted(BASELINE_TABLES)
        if name in Base.metadata.tables
    ]


def _dedupe_indexes() -> None:
    """Drop duplicate index objects that share a name within one table."""
    for table in Base.metadata.sorted_tables:
        seen: set[str] = set()
        for index in list(table.indexes):
            if index.name in seen:
                table.indexes.discard(index)
            else:
                seen.add(index.name or "")


def upgrade() -> None:
    """Create the baseline tables that do not exist yet."""
    _dedupe_indexes()
    Base.metadata.create_all(
        bind=op.get_bind(), tables=_baseline_tables(), checkfirst=True
    )


def downgrade() -> None:
    """Drop the baseline tables."""
    Base.metadata.drop_all(
        bind=op.get_bind(), tables=_baseline_tables(), checkfirst=True
    )
