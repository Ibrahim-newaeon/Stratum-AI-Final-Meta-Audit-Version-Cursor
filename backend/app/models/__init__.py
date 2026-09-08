# =============================================================================
# Stratum AI - Models Package
# =============================================================================
# Re-exports all models for backwards compatibility

# Base models (formerly models.py)
from app.base_models import (
    AdPlatform,
    APIKey,
    AssetType,
    AuditAction,
    AuditLog,
    Campaign,
    CampaignMetric,
    CampaignStatus,
    CompetitorBenchmark,
    CreativeAsset,
    MLPrediction,
    NotificationPreference,
    # Paddle Billing webhook idempotency ledger
    PaddleWebhookEvent,
    Rule,
    RuleAction,
    RuleExecution,
    RuleOperator,
    RuleStatus,
    # Models
    Tenant,
    User,
    # Enums
    UserRole,
    WhatsAppContact,
    WhatsAppConversation,
    WhatsAppMessage,
    WhatsAppMessageDirection,
    WhatsAppMessageStatus,
    WhatsAppOptInStatus,
    WhatsAppTemplate,
    WhatsAppTemplateCategory,
    WhatsAppTemplateStatus,
)

# Multi-Touch Attribution models
from app.models.attribution import (
    AttributionSnapshot,
    ChannelInteraction,
    ConversionPath,
    DailyAttributedRevenue,
    DataDrivenModelType,
    ModelStatus,
    ModelTrainingRun,
    TrainedAttributionModel,
)

# Audience Sync models
from app.models.audience_sync import (
    AudienceSyncCredential,
    AudienceSyncJob,
    AudienceType,
    PlatformAudience,
    SyncOperation,
    SyncPlatform,
    SyncStatus,
)

# Audit-recommended service models
from app.models.audit_services import (
    AudienceOverlapRecord,
    AudienceRecord,
    BudgetReallocationChange,
    BudgetReallocationPlan,
    ConversionLatency,
    ConversionLatencyStats,
    ConversionUploadStatus,
    Creative,
    CreativeFatigueAlert,
    CreativePerformance,
    CustomerLTVPrediction,
    CustomerSegment,
    EMQMeasurement,
    EMQStatus,
    ExperimentPrediction,
    ExperimentStatus,
    IndustryBenchmark,
    LTVCohortAnalysis,
    ModelExperiment,
    ModelRetrainingJob,
    OfflineConversion,
    OfflineConversionBatch,
    ReallocationStatus,
)

# Autopilot Enforcement models
from app.models.autopilot import (
    EnforcementAuditLog,
    EnforcementMode,
    InterventionAction,
    PendingConfirmationToken,
    TenantEnforcementRule,
    TenantEnforcementSettings,
    ViolationType,
)

# Campaign Builder models
from app.models.campaign_builder import (
    CampaignDraft,
    CampaignPublishLog,
    ConnectionStatus,
    DraftStatus,
    PublishResult,
    TenantAdAccount,
    TenantPlatformConnection,
)

# CAPI delivery telemetry. Registered here so Alembic sees the metadata:
# these tables were declared but never imported, so the baseline's create_all
# never built them and every reader of capi_delivery_logs failed at runtime.
from app.models.capi_delivery import (
    CAPIDeadLetterEntry,
    CAPIDeliveryDailyStats,
    CAPIDeliveryLog,
    CAPIEventDedupeRecord,
)

# CDP (Customer Data Platform) models
from app.models.cdp import (
    CDPConsent,
    CDPEvent,
    CDPProfile,
    CDPProfileIdentifier,
    CDPSource,
    ConsentType,
    IdentifierType,
    LifecycleStage,
    SourceType,
)

# Client Entity models (Agency model)
from app.models.client import (
    Client,
    ClientAssignment,
    ClientRequest,
    ClientRequestStatus,
    ClientRequestType,
)

# CMS (Content Management System) models
from app.models.cms import (
    CMSAuthor,
    CMSCategory,
    CMSContactSubmission,
    CMSContentType,
    CMSPage,
    CMSPageStatus,
    CMSPost,
    CMSPostStatus,
    CMSTag,
)

# CRM Integration models
from app.models.crm import (
    AttributionModel,
    CRMConnection,
    CRMConnectionStatus,
    CRMContact,
    CRMDeal,
    CRMProvider,
    CRMWritebackConfig,
    CRMWritebackSync,
    DailyPipelineMetrics,
    DealStage,
    Touchpoint,
    WritebackStatus,
)

# Embeddable widget models
from app.models.embed_widgets import (
    BrandingLevel,
    EmbedDomainWhitelist,
    EmbedToken,
    EmbedWidget,
    EmbedWidgetView,
    TokenStatus,
    WidgetSize,
    WidgetType,
)

# Measurement & Verification models (GA4 read-only baseline + GTM tag deployment)
from app.models.measurement import (
    FactGA4Daily,
    MeasurementProvider,
    MeasurementStatus,
    TenantGA4Integration,
    TenantGTMIntegration,
)

# Meta privacy callback models (App Review: data deletion request records)
from app.models.meta_privacy import (
    DataDeletionStatus,
    MetaDataDeletionRequest,
)

# Social login identities (Facebook Login)
from app.models.social_identity import (
    SocialProvider,
    UserSocialIdentity,
)

# Onboarding models
from app.models.onboarding import (
    AutomationMode,
    Industry,
    MonthlyAdSpend,
    OnboardingStatus,
    OnboardingStep,
    PrimaryKPI,
    TeamSize,
    TenantOnboarding,
)

# Pacing & Forecasting models
from app.models.pacing import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    DailyKPI,
    Forecast,
    PacingAlert,
    PacingSummary,
    Target,
    TargetMetric,
    TargetPeriod,
)

# Profit ROAS models
from app.models.profit import (
    COGSSource,
    COGSUpload,
    DailyProfitMetrics,
    MarginRule,
    MarginType,
    ProductCatalog,
    ProductMargin,
    ProductStatus,
    ProfitROASReport,
)

# Automated Reporting models
from app.models.reporting import (
    DeliveryChannel,
    DeliveryChannelConfig,
    DeliveryStatus,
    ExecutionStatus,
    ReportDelivery,
    ReportExecution,
    ReportFormat,
    ReportTemplate,
    ReportType,
    ScheduledReport,
    ScheduleFrequency,
)

# Settings models (Webhooks, Notifications, Changelog, Slack)
from app.models.settings import (
    ChangelogEntry,
    ChangelogReadStatus,
    ChangelogType,
    Notification,
    NotificationCategory,
    NotificationType,
    SlackIntegration,
    Webhook,
    WebhookDelivery,
    WebhookEventType,
    WebhookStatus,
)

# Trust Layer models
from app.models.trust_layer import (
    AttributionVarianceStatus,
    FactActionsQueue,
    FactAttributionVarianceDaily,
    FactSignalHealthDaily,
    SignalHealthStatus,
)

__all__ = [
    # Enums
    "UserRole",
    "AdPlatform",
    "CampaignStatus",
    "AssetType",
    "RuleStatus",
    "RuleOperator",
    "RuleAction",
    "AuditAction",
    "WhatsAppOptInStatus",
    "WhatsAppMessageDirection",
    "WhatsAppMessageStatus",
    "WhatsAppTemplateStatus",
    "WhatsAppTemplateCategory",
    "SignalHealthStatus",
    "AttributionVarianceStatus",
    "ConnectionStatus",
    "DraftStatus",
    "PublishResult",
    # Models
    "Tenant",
    "User",
    "Campaign",
    "CampaignMetric",
    "CreativeAsset",
    "Rule",
    "RuleExecution",
    "CompetitorBenchmark",
    "AuditLog",
    "MLPrediction",
    "NotificationPreference",
    "APIKey",
    "PaddleWebhookEvent",
    "WhatsAppContact",
    "WhatsAppTemplate",
    "WhatsAppMessage",
    "WhatsAppConversation",
    "FactSignalHealthDaily",
    "FactAttributionVarianceDaily",
    "FactActionsQueue",
    "TenantPlatformConnection",
    "TenantAdAccount",
    "CampaignDraft",
    "CampaignPublishLog",
    # Meta privacy callbacks (App Review)
    "DataDeletionStatus",
    "MetaDataDeletionRequest",
    # CRM Integration
    "CRMProvider",
    "CRMConnectionStatus",
    "DealStage",
    "AttributionModel",
    "WritebackStatus",
    "CRMConnection",
    "CRMContact",
    "CRMDeal",
    "Touchpoint",
    "DailyPipelineMetrics",
    "CRMWritebackConfig",
    "CRMWritebackSync",
    # Pacing & Forecasting
    "TargetPeriod",
    "TargetMetric",
    "AlertSeverity",
    "AlertType",
    "AlertStatus",
    "Target",
    "DailyKPI",
    "PacingAlert",
    "Forecast",
    "PacingSummary",
    # Profit ROAS
    "MarginType",
    "ProductStatus",
    "COGSSource",
    "ProductCatalog",
    "ProductMargin",
    "MarginRule",
    "DailyProfitMetrics",
    "ProfitROASReport",
    "COGSUpload",
    # Multi-Touch Attribution
    "DailyAttributedRevenue",
    "ConversionPath",
    "AttributionSnapshot",
    "ChannelInteraction",
    # Data-Driven Attribution
    "DataDrivenModelType",
    "ModelStatus",
    "TrainedAttributionModel",
    "ModelTrainingRun",
    # Automated Reporting
    "ReportType",
    "ReportFormat",
    "ScheduleFrequency",
    "DeliveryChannel",
    "ExecutionStatus",
    "DeliveryStatus",
    "ReportTemplate",
    "ScheduledReport",
    "ReportExecution",
    "ReportDelivery",
    "DeliveryChannelConfig",
    # Onboarding
    "OnboardingStatus",
    "OnboardingStep",
    "Industry",
    "MonthlyAdSpend",
    "TeamSize",
    "AutomationMode",
    "PrimaryKPI",
    "TenantOnboarding",
    # Autopilot Enforcement
    "EnforcementMode",
    "ViolationType",
    "InterventionAction",
    "TenantEnforcementSettings",
    "TenantEnforcementRule",
    "EnforcementAuditLog",
    "PendingConfirmationToken",
    # CDP (Customer Data Platform)
    "SourceType",
    "IdentifierType",
    "LifecycleStage",
    "ConsentType",
    "CDPSource",
    "CDPProfile",
    "CDPProfileIdentifier",
    "CDPEvent",
    "CDPConsent",
    # Audience Sync
    "SyncPlatform",
    "SyncStatus",
    "SyncOperation",
    "AudienceType",
    "PlatformAudience",
    "AudienceSyncJob",
    "AudienceSyncCredential",
    # Settings (Webhooks, Notifications, Changelog, Slack)
    "WebhookStatus",
    "WebhookEventType",
    "NotificationType",
    "NotificationCategory",
    "ChangelogType",
    "Webhook",
    "WebhookDelivery",
    "Notification",
    "ChangelogEntry",
    "ChangelogReadStatus",
    "SlackIntegration",
    # Measurement & Verification (GA4 read-only + GTM tag deployment)
    "MeasurementProvider",
    "MeasurementStatus",
    "TenantGA4Integration",
    "TenantGTMIntegration",
    "FactGA4Daily",
    # Client Entity (Agency model)
    "Client",
    "ClientAssignment",
    "ClientRequest",
    "ClientRequestStatus",
    "ClientRequestType",
    # CMS (Content Management System)
    "CMSPostStatus",
    "CMSContentType",
    "CMSPageStatus",
    "CMSCategory",
    "CMSTag",
    "CMSAuthor",
    "CMSPost",
    "CMSPage",
    "CMSContactSubmission",
    # CAPI delivery telemetry
    "CAPIDeliveryLog",
    "CAPIDeadLetterEntry",
    "CAPIEventDedupeRecord",
    "CAPIDeliveryDailyStats",
    # Audit-recommended service models
    "AudienceOverlapRecord",
    "AudienceRecord",
    "BudgetReallocationChange",
    "BudgetReallocationPlan",
    "ConversionLatency",
    "ConversionLatencyStats",
    "ConversionUploadStatus",
    "Creative",
    "CreativeFatigueAlert",
    "CreativePerformance",
    "CustomerLTVPrediction",
    "CustomerSegment",
    "EMQMeasurement",
    "EMQStatus",
    "ExperimentPrediction",
    "ExperimentStatus",
    "IndustryBenchmark",
    "LTVCohortAnalysis",
    "ModelExperiment",
    "ModelRetrainingJob",
    "OfflineConversion",
    "OfflineConversionBatch",
    "ReallocationStatus",
    # Embeddable widget models
    "BrandingLevel",
    "EmbedDomainWhitelist",
    "EmbedToken",
    "EmbedWidget",
    "EmbedWidgetView",
    "TokenStatus",
    "WidgetSize",
    "WidgetType",
    # Social login identity models
    "SocialProvider",
    "UserSocialIdentity",
]
