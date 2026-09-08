# =============================================================================
# Stratum AI - API v1 Router Configuration
# =============================================================================
"""
Main API router that aggregates all endpoint routers.
"""

from fastapi import APIRouter, Depends

from app.api.v1.endpoints import (
    analytics,
    analytics_ai,
    # New Settings endpoints
    api_keys,
    assets,
    attribution,
    audience_sync,
    audit_services,
    auth,
    auth_facebook,
    autopilot,
    autopilot_enforcement,
    # Billing (Paddle Billing)
    billing,
    campaign_builder,
    campaigns,
    capi,
    cdp,
    changelog,
    # Client Management (Agency model)
    clients,
    # CMS (Content Management System)
    cms,
    competitors,
    dashboard,
    data_driven_attribution,
    emq_v2,
    feature_flags,
    gdpr,
    insights,
    integrations,
    knowledge_graph,
    landing_cms,
    # Measurement & Verification (GA4 read-only baseline + GTM tag deployment)
    measurement,
    # Meta App Review privacy callbacks (public, signed_request-verified)
    meta_callbacks,
    meta_capi,
    mfa,
    ml_training,
    notifications,
    oauth,
    onboarding,
    onboarding_agent,
    pacing,
    # Paddle Webhooks (public, signature-verified notification endpoint)
    paddle_webhook,
    predictions,
    profit,
    qa_fixes,
    reporting,
    rules,
    simulator,
    slack,
    subscription,
    superadmin,
    superadmin_analytics,
    tenant_dashboard,
    tenants,
    tier,
    trust_layer,
    users,
    webhooks,
    whatsapp,
)
from app.api.v1.guards import require_authenticated_request

# Router-level authentication guard (defense in depth).
#
# It is attached to api_router itself rather than to each non-public
# include_router() call below, on purpose:
#   * omission is impossible - a router added later is guarded automatically,
#     which is exactly the failure mode being fixed (39 of 59 endpoint modules
#     never imported get_current_user and no include_router passed a dependency,
#     so TenantMiddleware was their only authentication);
#   * routers that mix public and authenticated paths are handled correctly.
#     cdp.router carries both the key-authenticated /cdp/ingest and ~60
#     tenant-scoped routes, and webhooks/auth are the same - a per-router
#     dependencies=[...] list cannot express that split, so those routers would
#     have had to stay unguarded;
#   * the public exemption stays in one place. The guard defers to
#     app.middleware.tenant.is_public_endpoint, the same predicate the
#     middleware skips on, so the middleware and the guard can never disagree
#     about which routes are public.
# The guard reads request.state only (no database query); endpoints that need
# the user record still depend on get_current_user.
api_router = APIRouter(dependencies=[Depends(require_authenticated_request)])

# Authentication
api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["Authentication"],
)

# "Log in with Facebook". Separate module, same /auth prefix: it shares the
# LoginResponse shape and the MFA session mechanism with auth.py but has its own
# Graph verification, account-resolution and link-management surface.
api_router.include_router(
    auth_facebook.router,
    prefix="/auth",
    tags=["Authentication"],
)

# OAuth (Ad Platform Connections)
api_router.include_router(
    oauth.router,
    tags=["OAuth"],
)

# Onboarding Wizard
api_router.include_router(
    onboarding.router,
    tags=["Onboarding"],
)

# Conversational Onboarding Agent
api_router.include_router(
    onboarding_agent.router,
    tags=["Onboarding Agent"],
)

# Main Dashboard (Unified dashboard for frontend)
api_router.include_router(
    dashboard.router,
    tags=["Dashboard"],
)

# User management
api_router.include_router(
    users.router,
    prefix="/users",
    tags=["Users"],
)

# Tenant management
api_router.include_router(
    tenants.router,
    prefix="/tenants",
    tags=["Tenants"],
)

# Campaigns (Module B)
api_router.include_router(
    campaigns.router,
    prefix="/campaigns",
    tags=["Campaigns"],
)

# Client Management (Agency model)
api_router.include_router(
    clients.router,
    prefix="/clients",
    tags=["Clients"],
)

# Creative Assets / DAM (Module B)
api_router.include_router(
    assets.router,
    prefix="/assets",
    tags=["Digital Assets"],
)

# Automation Rules (Module C)
api_router.include_router(
    rules.router,
    prefix="/rules",
    tags=["Automation Rules"],
)

# Competitor Intelligence (Module D)
api_router.include_router(
    competitors.router,
    prefix="/competitors",
    tags=["Competitor Intelligence"],
)

# ML Simulator (Module A)
api_router.include_router(
    simulator.router,
    prefix="/simulate",
    tags=["ML Simulator"],
)

# Analytics & Dashboard
api_router.include_router(
    analytics.router,
    prefix="/analytics",
    tags=["Analytics"],
)

# GDPR Compliance (Module F)
api_router.include_router(
    gdpr.router,
    prefix="/gdpr",
    tags=["GDPR Compliance"],
)

# WhatsApp Integration (Module G)
api_router.include_router(
    whatsapp.router,
    prefix="/whatsapp",
    tags=["WhatsApp"],
)

# ML Training & Data Upload
api_router.include_router(
    ml_training.router,
    prefix="/ml",
    tags=["ML Training"],
)

# Live Predictions & ROAS Optimization
api_router.include_router(
    predictions.router,
    prefix="/predictions",
    tags=["Live Predictions"],
)

# Conversion API (CAPI) Integration
api_router.include_router(
    capi.router,
    prefix="/capi",
    tags=["Conversion API"],
)

# Meta CAPI QA (Event collection with quality tracking)
api_router.include_router(
    meta_capi.router,
    tags=["Meta CAPI QA"],
)

# Landing Page CMS (Multi-language content management)
api_router.include_router(
    landing_cms.router,
    tags=["Landing CMS"],
)

# EMQ One-Click Fix System
api_router.include_router(
    qa_fixes.router,
    tags=["QA Fixes"],
)

# AI-Powered Analytics (Scaling scores, fatigue, anomalies, recommendations)
api_router.include_router(
    analytics_ai.router,
    prefix="/analytics/ai",
    tags=["AI Analytics"],
)

# Super Admin Dashboard (Platform-level management)
api_router.include_router(
    superadmin.router,
    prefix="/superadmin",
    tags=["Super Admin"],
)

# Tenant Dashboard (Tenant-scoped analytics and settings)
api_router.include_router(
    tenant_dashboard.router,
    prefix="/tenant",
    tags=["Tenant Dashboard"],
)

# Superadmin Analytics (Platform-wide analytics)
api_router.include_router(
    superadmin_analytics.router,
    prefix="/superadmin/analytics",
    tags=["Superadmin Analytics"],
)

# Autopilot (Automated campaign optimization)
api_router.include_router(
    autopilot.router,
    prefix="/autopilot",
    tags=["Autopilot"],
)

# Autopilot Enforcement (Budget/ROAS restrictions)
api_router.include_router(
    autopilot_enforcement.router,
    tags=["Autopilot Enforcement"],
)

# Campaign Builder (Multi-platform campaign creation)
api_router.include_router(
    campaign_builder.router,
    prefix="/campaign-builder",
    tags=["Campaign Builder"],
)

# Feature Flags (Feature toggles and rollouts)
api_router.include_router(
    feature_flags.router,
    prefix="/features",
    tags=["Feature Flags"],
)

# AI Insights (Intelligent recommendations)
api_router.include_router(
    insights.router,
    prefix="/insights",
    tags=["AI Insights"],
)

# Trust Layer (Data quality and signal health)
api_router.include_router(
    trust_layer.router,
    prefix="/trust",
    tags=["Trust Layer"],
)

# EMQ v2 (Event Measurement Quality - Enhanced)
api_router.include_router(
    emq_v2.router,
    tags=["EMQ v2"],
)

# Measurement & Verification (GA4 read-only baseline, GTM tag deployment)
# Registered BEFORE integrations.router so the literal /integrations/measurement
# paths win over any /integrations/{provider} parameter routes.
api_router.include_router(
    measurement.router,
    tags=["Measurement & Verification"],
)

# Integrations (HubSpot, CRM, Attribution)
api_router.include_router(
    integrations.router,
    tags=["Integrations"],
)

# Pacing & Forecasting (Targets, Pacing Alerts, EOM Projections)
api_router.include_router(
    pacing.router,
    prefix="/pacing",
    tags=["Pacing & Forecasting"],
)

# Profit ROAS (Products, COGS, Profit Calculations)
api_router.include_router(
    profit.router,
    prefix="/profit",
    tags=["Profit ROAS"],
)

# Multi-Touch Attribution (MTA)
api_router.include_router(
    attribution.router,
    tags=["Attribution"],
)

# Data-Driven Attribution (ML-based)
api_router.include_router(
    data_driven_attribution.router,
    tags=["Data-Driven Attribution"],
)

# Automated Reporting (Scheduled reports, PDF generation, multi-channel delivery)
api_router.include_router(
    reporting.router,
    prefix="/reporting",
    tags=["Automated Reporting"],
)

# Audit Services (EMQ, Offline Conversions, A/B Testing, LTV, etc.)
api_router.include_router(
    audit_services.router,
    prefix="/audit",
    tags=["Audit Services"],
)

# CDP (Customer Data Platform - Event ingestion, profiles, identity resolution)
api_router.include_router(
    cdp.router,
    tags=["CDP"],
)

# CDP Audience Sync (Push segments to ad platforms)
api_router.include_router(
    audience_sync.router,
    tags=["CDP Audience Sync"],
)

# Subscription Tier (Feature access and limits)
api_router.include_router(
    tier.router,
    tags=["Subscription Tier"],
)

# Subscription Status (Expiry, billing, warnings)
api_router.include_router(
    subscription.router,
    tags=["Subscription"],
)

# Billing (Paddle Billing)
api_router.include_router(
    billing.router,
    tags=["Billing"],
)

# Paddle Webhooks (public, signature-verified notification endpoint)
api_router.include_router(
    paddle_webhook.router,
    tags=["Paddle Webhooks"],
)

# Meta App Review privacy callbacks (public, signed_request-verified):
# Deauthorize Callback, Data Deletion Request Callback and its status page.
api_router.include_router(
    meta_callbacks.router,
    tags=["Meta App Review Callbacks"],
)

# MFA (Two-Factor Authentication)
api_router.include_router(
    mfa.router,
    tags=["MFA"],
)

# API Keys Management
api_router.include_router(
    api_keys.router,
    tags=["API Keys"],
)

# Webhooks Management
api_router.include_router(
    webhooks.router,
    tags=["Webhooks"],
)

# In-App Notifications
api_router.include_router(
    notifications.router,
    tags=["Notifications"],
)

# Changelog / What's New
api_router.include_router(
    changelog.router,
    tags=["Changelog"],
)

# Slack Integration
api_router.include_router(
    slack.router,
    tags=["Slack Integration"],
)

# CMS (Content Management System - Blog, Pages, Contact)
api_router.include_router(
    cms.router,
    tags=["CMS"],
)

# Knowledge Graph (Revenue attribution, Trust explainability, Insights)
api_router.include_router(
    knowledge_graph.router,
    prefix="/knowledge-graph",
    tags=["Knowledge Graph"],
)
