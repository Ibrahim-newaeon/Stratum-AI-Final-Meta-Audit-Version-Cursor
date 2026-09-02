/**
 * Embed Widgets - Types and constants for embeddable widgets.
 */

export type WidgetType =
  | 'signal_health'
  | 'roas_display'
  | 'campaign_performance'
  | 'trust_gate_status'
  | 'spend_tracker'
  | 'anomaly_alert';

export type WidgetSize = 'badge' | 'compact' | 'standard' | 'large' | 'custom';

export type BrandingLevel = 'full' | 'minimal' | 'none';

export interface EmbedWidget {
  id: string;
  name: string;
  description?: string;
  widget_type: WidgetType;
  widget_size: WidgetSize;
  branding_level: BrandingLevel;
  data_scope: {
    date_range_days?: number;
    campaign_ids?: string[];
    [key: string]: unknown;
  };
  refresh_interval_seconds: number;
  is_active: boolean;
  total_views: number;
  custom_width?: number;
  custom_height?: number;
  created_at: string;
  updated_at: string;
}

export interface DomainWhitelist {
  id: string;
  domain_pattern: string;
  is_verified: boolean;
  is_active: boolean;
  description?: string;
  created_at: string;
}

export const WIDGET_DIMENSIONS: Record<Exclude<WidgetSize, 'custom'>, { width: number; height: number }> = {
  badge: { width: 120, height: 40 },
  compact: { width: 200, height: 100 },
  standard: { width: 300, height: 200 },
  large: { width: 400, height: 300 },
};

export const WIDGET_TYPE_INFO: Record<WidgetType, { label: string; description: string }> = {
  signal_health: {
    label: 'Signal Health',
    description: 'Live signal health score badge with trust status.',
  },
  roas_display: {
    label: 'ROAS Display',
    description: 'Current return on ad spend for the selected period.',
  },
  campaign_performance: {
    label: 'Campaign Performance',
    description: 'Compact table of top campaign metrics.',
  },
  trust_gate_status: {
    label: 'Trust Gate Status',
    description: 'Current trust gate state (pass / hold / block).',
  },
  spend_tracker: {
    label: 'Spend Tracker',
    description: 'Ad spend pacing against the configured budget.',
  },
  anomaly_alert: {
    label: 'Anomaly Alert',
    description: 'Latest detected anomalies in signal or event data.',
  },
};
