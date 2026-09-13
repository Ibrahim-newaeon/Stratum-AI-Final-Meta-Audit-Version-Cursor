/**
 * Data Quality Dashboard
 * AI-powered data gap analysis for Conversion APIs
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  CheckCircle,
  ChevronRight,
  Lightbulb,
  Plug,
  RefreshCw,
  Target,
  TrendingDown,
  TrendingUp,
  XCircle,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { apiClient } from '@/api/client';

interface DataGap {
  field: string;
  severity: string;
  impact_percent: number;
  recommendation: string;
  how_to_fix: string;
}

interface PlatformScore {
  score: number;
  quality_level: string;
  potential_roas_lift: number;
  fields_present: string[];
  fields_missing: string[];
  data_gaps: DataGap[];
}

interface QualityReport {
  overall_score: number;
  estimated_roas_improvement: number;
  data_gaps_summary: {
    critical: number;
    high: number;
    medium: number;
    low: number;
  };
  trend: string;
  platform_scores: Record<string, PlatformScore>;
  top_recommendations: Array<{
    priority: number;
    field: string;
    action: string;
    how_to_fix: string;
    impact: string;
    severity: string;
    affected_platforms: string[];
  }>;
}

const PLATFORM_NAMES: Record<string, string> = {
  meta: 'Meta',
  facebook: 'Facebook',
  instagram: 'Instagram',
  whatsapp: 'WhatsApp',
};

const PLATFORM_COLORS: Record<string, string> = {
  meta: 'bg-blue-500',
  facebook: 'bg-blue-600',
  instagram: 'bg-pink-500',
  whatsapp: 'bg-green-500',
};

const getScoreColor = (score: number) => {
  if (score >= 80) return 'text-green-500';
  if (score >= 60) return 'text-blue-500';
  if (score >= 40) return 'text-amber-500';
  return 'text-red-500';
};

const getScoreBg = (score: number) => {
  if (score >= 80) return 'bg-green-500/10';
  if (score >= 60) return 'bg-blue-500/10';
  if (score >= 40) return 'bg-amber-500/10';
  return 'bg-red-500/10';
};

const getQualityLabel = (level: string) => {
  const labels: Record<string, { label: string; color: string }> = {
    Excellent: { label: 'Excellent', color: 'text-green-500 bg-green-500/10' },
    Good: { label: 'Good', color: 'text-blue-500 bg-blue-500/10' },
    Fair: { label: 'Fair', color: 'text-amber-500 bg-amber-500/10' },
    Poor: { label: 'Needs Work', color: 'text-red-500 bg-red-500/10' },
  };
  return labels[level] || labels.Poor;
};

const getSeverityStyle = (severity: string) => {
  const styles: Record<string, string> = {
    critical: 'bg-red-500/10 text-red-500 border-red-500/20',
    high: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
    medium: 'bg-amber-500/10 text-amber-500 border-amber-500/20',
    low: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  };
  return styles[severity] || styles.medium;
};

type EmptyStateKind = 'connect' | 'waiting_for_events' | null;

export function DataQuality() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [report, setReport] = useState<QualityReport | null>(null);
  const [selectedPlatform, setSelectedPlatform] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [emptyState, setEmptyState] = useState<EmptyStateKind>(null);
  const [connectedPlatforms, setConnectedPlatforms] = useState<string[]>([]);

  const fetchReport = async () => {
    try {
      setRefreshing(true);
      setError(null);

      const statusResponse = await apiClient.get('/capi/platforms/status');
      const connected = Object.keys(
        statusResponse.data?.data?.connected_platforms || {}
      );
      setConnectedPlatforms(connected);

      const response = await apiClient.get('/capi/quality/report');
      const raw = response.data?.data ?? {};
      // Backend emits overall_score / platform_scores.
      const overall = Number(raw.overall_score ?? 0);
      const platformScores = (raw.platform_scores || {}) as Record<
        string,
        PlatformScore
      >;
      const data: QualityReport | null =
        overall > 0
          ? {
              overall_score: overall,
              estimated_roas_improvement: raw.estimated_roas_improvement ?? 0,
              data_gaps_summary: raw.data_gaps_summary || {
                critical: 0,
                high: 0,
                medium: 0,
                low: 0,
              },
              trend: raw.trend || 'stable',
              platform_scores: platformScores,
              top_recommendations: raw.top_recommendations || [],
            }
          : null;

      if (data && data.overall_score > 0 && Object.keys(data.platform_scores).length > 0) {
        setReport(data);
        setEmptyState(null);
        if (!selectedPlatform) {
          setSelectedPlatform(Object.keys(data.platform_scores)[0]);
        }
      } else {
        setReport(null);
        setEmptyState(connected.length > 0 ? 'waiting_for_events' : 'connect');
      }
    } catch (err) {
      console.error('Failed to fetch report:', err);
      setReport(null);
      setEmptyState(null);
      setError('Failed to load data quality report');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchReport();
    // Refresh every 2 minutes
    const interval = setInterval(fetchReport, 2 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  const selectedScore = selectedPlatform ? report?.platform_scores[selectedPlatform] : null;

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="animate-pulse space-y-4">
          <div className="h-8 bg-muted rounded w-1/3" />
          <div className="h-40 bg-muted rounded" />
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="h-80 bg-muted rounded" />
            <div className="h-80 bg-muted rounded" />
          </div>
        </div>
      </div>
    );
  }

  if (!report || report.overall_score === 0) {
    const platformLabels = connectedPlatforms.map(
      (platform) => PLATFORM_NAMES[platform] || platform
    );

    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Data Quality Dashboard</h1>
          <p className="text-muted-foreground mt-1">Monitor and improve your Event Match Quality</p>
        </div>

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/40 dark:text-red-300">
            {error}
          </div>
        )}

        <div className="flex flex-col items-center justify-center py-16 text-center">
          <Activity className="w-16 h-16 text-muted-foreground/50 mb-4" />
          {emptyState === 'waiting_for_events' ? (
            <>
              <h2 className="text-xl font-semibold text-foreground mb-2">Waiting for Events</h2>
              <p className="text-muted-foreground max-w-md">
                {platformLabels.length > 0
                  ? `${platformLabels.join(', ')} ${platformLabels.length === 1 ? 'is' : 'are'} connected. Stream conversion events through CAPI to see match quality scores here.`
                  : 'Your platforms are connected. Stream conversion events through CAPI to see match quality scores here.'}
              </p>
              {platformLabels.length > 0 && (
                <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
                  {platformLabels.map((label) => (
                    <span
                      key={label}
                      className="inline-flex items-center gap-1 rounded-full bg-green-500/10 px-3 py-1 text-xs font-medium text-green-600"
                    >
                      <CheckCircle className="h-3.5 w-3.5" />
                      {label} connected
                    </span>
                  ))}
                </div>
              )}
              <Link
                to="/dashboard/capi-setup"
                className="mt-6 inline-flex items-center gap-2 px-6 py-3 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors"
              >
                Open CAPI Setup
                <ChevronRight className="w-4 h-4" />
              </Link>
            </>
          ) : (
            <>
              <h2 className="text-xl font-semibold text-foreground mb-2">No Data Yet</h2>
              <p className="text-muted-foreground max-w-md">
                Connect Meta or WhatsApp in CAPI Setup, then stream conversion events to see your
                data quality analysis.
              </p>
              <Link
                to="/dashboard/capi-setup"
                className="mt-6 inline-flex items-center gap-2 px-6 py-3 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors"
              >
                <Plug className="w-4 h-4" />
                Connect Platforms
                <ChevronRight className="w-4 h-4" />
              </Link>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Data Quality Dashboard</h1>
          <p className="text-muted-foreground mt-1">
            AI-powered analysis to boost your Event Match Quality and ROAS
          </p>
        </div>
        <button
          onClick={fetchReport}
          disabled={refreshing}
          className="flex items-center gap-2 px-4 py-2 bg-muted hover:bg-muted/80 rounded-lg transition-colors disabled:opacity-50"
        >
          <RefreshCw className={cn('w-4 h-4', refreshing && 'animate-spin')} />
          Refresh
        </button>
      </div>

      {/* Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Overall Score */}
        <div className={cn('rounded-xl border p-5', getScoreBg(report.overall_score))}>
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm text-muted-foreground">Overall Score</span>
            <Activity className={cn('w-5 h-5', getScoreColor(report.overall_score))} />
          </div>
          <div className={cn('text-3xl font-bold', getScoreColor(report.overall_score))}>
            {report.overall_score}%
          </div>
          <div className="mt-2 flex items-center gap-1 text-sm">
            {report.trend === 'improving' ? (
              <>
                <TrendingUp className="w-4 h-4 text-green-500" />
                <span className="text-green-500">Improving</span>
              </>
            ) : report.trend === 'declining' ? (
              <>
                <TrendingDown className="w-4 h-4 text-red-500" />
                <span className="text-red-500">Declining</span>
              </>
            ) : (
              <span className="text-muted-foreground">Stable</span>
            )}
          </div>
        </div>

        {/* ROAS Potential */}
        <div className="rounded-xl border bg-gradient-to-br from-green-500/10 to-emerald-500/10 p-5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm text-muted-foreground">Potential ROAS Lift</span>
            <TrendingUp className="w-5 h-5 text-green-500" />
          </div>
          <div className="text-3xl font-bold text-green-500">
            +{report.estimated_roas_improvement}%
          </div>
          <p className="mt-2 text-sm text-muted-foreground">Fix data gaps to unlock</p>
        </div>

        {/* Critical Gaps */}
        <div className="rounded-xl border bg-card p-5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm text-muted-foreground">Critical Gaps</span>
            <AlertTriangle className="w-5 h-5 text-red-500" />
          </div>
          <div className="text-3xl font-bold text-foreground">
            {report.data_gaps_summary.critical + report.data_gaps_summary.high}
          </div>
          <div className="mt-2 flex gap-2">
            <span className="px-2 py-0.5 text-xs rounded-full bg-red-500/10 text-red-500">
              {report.data_gaps_summary.critical} critical
            </span>
            <span className="px-2 py-0.5 text-xs rounded-full bg-orange-500/10 text-orange-500">
              {report.data_gaps_summary.high} high
            </span>
          </div>
        </div>

        {/* Platforms */}
        <div className="rounded-xl border bg-card p-5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm text-muted-foreground">Platforms</span>
            <Target className="w-5 h-5 text-primary" />
          </div>
          <div className="text-3xl font-bold text-foreground">
            {Object.keys(report.platform_scores).length}
          </div>
          <p className="mt-2 text-sm text-muted-foreground">Active connections</p>
        </div>
      </div>

      {/* Main Content */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Platform Scores */}
        <div className="lg:col-span-2 rounded-xl border bg-card overflow-hidden">
          <div className="px-6 py-4 border-b">
            <h2 className="font-semibold text-foreground">Platform Match Quality</h2>
          </div>

          {/* Platform Tabs */}
          <div className="flex border-b overflow-x-auto">
            {Object.entries(report.platform_scores).map(([platform, score]) => (
              <button
                key={platform}
                onClick={() => setSelectedPlatform(platform)}
                className={cn(
                  'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap',
                  selectedPlatform === platform
                    ? 'border-primary text-primary'
                    : 'border-transparent text-muted-foreground hover:text-foreground'
                )}
              >
                <div className={cn('w-3 h-3 rounded-full', PLATFORM_COLORS[platform])} />
                {PLATFORM_NAMES[platform] || platform}
                <span
                  className={cn(
                    'px-1.5 py-0.5 text-xs rounded',
                    getScoreBg(score.score),
                    getScoreColor(score.score)
                  )}
                >
                  {score.score}%
                </span>
              </button>
            ))}
          </div>

          {/* Platform Details */}
          {selectedScore && (
            <div className="p-6 space-y-6">
              {/* Score Overview */}
              <div className="flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-3">
                    <span className={cn('text-4xl font-bold', getScoreColor(selectedScore.score))}>
                      {selectedScore.score}%
                    </span>
                    <span
                      className={cn(
                        'px-3 py-1 rounded-full text-sm font-medium',
                        getQualityLabel(selectedScore.quality_level).color
                      )}
                    >
                      {getQualityLabel(selectedScore.quality_level).label}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">Event Match Quality Score</p>
                </div>
                <div className="text-right">
                  <div className="text-2xl font-bold text-green-500">
                    +{selectedScore.potential_roas_lift}%
                  </div>
                  <p className="text-sm text-muted-foreground">ROAS lift potential</p>
                </div>
              </div>

              {/* Fields Status */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <h4 className="text-sm font-medium text-foreground mb-2 flex items-center gap-2">
                    <CheckCircle className="w-4 h-4 text-green-500" />
                    Fields Present ({selectedScore.fields_present.length})
                  </h4>
                  <div className="flex flex-wrap gap-2">
                    {selectedScore.fields_present.map((field) => (
                      <span
                        key={field}
                        className="px-2 py-1 text-xs bg-green-500/10 text-green-600 rounded-full"
                      >
                        {field}
                      </span>
                    ))}
                  </div>
                </div>
                <div>
                  <h4 className="text-sm font-medium text-foreground mb-2 flex items-center gap-2">
                    <XCircle className="w-4 h-4 text-red-500" />
                    Fields Missing ({selectedScore.fields_missing.length})
                  </h4>
                  <div className="flex flex-wrap gap-2">
                    {selectedScore.fields_missing.map((field) => (
                      <span
                        key={field}
                        className="px-2 py-1 text-xs bg-red-500/10 text-red-600 rounded-full"
                      >
                        {field}
                      </span>
                    ))}
                  </div>
                </div>
              </div>

              {/* Data Gaps */}
              <div>
                <h4 className="text-sm font-medium text-foreground mb-3">Data Gaps to Fix</h4>
                <div className="space-y-3">
                  {selectedScore.data_gaps.map((gap, idx) => (
                    <div
                      key={idx}
                      className={cn('p-4 rounded-lg border', getSeverityStyle(gap.severity))}
                    >
                      <div className="flex items-start justify-between">
                        <div>
                          <div className="flex items-center gap-2 mb-1">
                            <span className="font-medium">{gap.field}</span>
                            <span
                              className={cn(
                                'px-2 py-0.5 text-xs rounded-full uppercase font-semibold',
                                gap.severity === 'critical'
                                  ? 'bg-red-500 text-white'
                                  : gap.severity === 'high'
                                    ? 'bg-orange-500 text-white'
                                    : 'bg-amber-500 text-white'
                              )}
                            >
                              {gap.severity}
                            </span>
                          </div>
                          <p className="text-sm">{gap.recommendation}</p>
                        </div>
                        <span className="text-sm font-semibold">+{gap.impact_percent}%</span>
                      </div>
                      <div className="mt-2 p-2 bg-background/50 rounded text-xs">
                        <strong>How to fix:</strong> {gap.how_to_fix}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Recommendations Panel */}
        <div className="rounded-xl border bg-card overflow-hidden">
          <div className="px-6 py-4 border-b bg-gradient-to-r from-primary/5 to-transparent">
            <div className="flex items-center gap-2">
              <Lightbulb className="w-5 h-5 text-primary" />
              <h2 className="font-semibold text-foreground">Top Recommendations</h2>
            </div>
          </div>

          <div className="p-4 space-y-3">
            {report.top_recommendations.map((rec, idx) => (
              <div
                key={idx}
                className="p-4 rounded-lg bg-muted/50 hover:bg-muted transition-colors"
              >
                <div className="flex items-start gap-3">
                  <div
                    className={cn(
                      'w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold text-white',
                      rec.priority === 1
                        ? 'bg-red-500'
                        : rec.priority === 2
                          ? 'bg-orange-500'
                          : 'bg-amber-500'
                    )}
                  >
                    {rec.priority}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-medium text-foreground">{rec.field}</span>
                      <span className="text-xs text-green-500 font-semibold">{rec.impact}</span>
                    </div>
                    <p className="text-sm text-muted-foreground mb-2">{rec.action}</p>
                    <div className="flex flex-wrap gap-1">
                      {rec.affected_platforms.map((p) => (
                        <span
                          key={p}
                          className="px-2 py-0.5 text-xs bg-primary/10 text-primary rounded"
                        >
                          {PLATFORM_NAMES[p] || p}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="px-6 py-4 border-t bg-muted/30">
            <a
              href="/dashboard/capi-setup"
              className="flex items-center justify-center gap-2 text-sm text-primary hover:underline"
            >
              Configure Platform Settings
              <ArrowUpRight className="w-4 h-4" />
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

export default DataQuality;
