/**
 * SignalHealthCard - Signal health summary display
 *
 * Signal health has four states, not three. `insufficient_data` means the
 * trust engine could not measure enough of the tenant's signal to judge it,
 * and it is rendered as an explicit gap listing what is missing - never as a
 * zero (which reads as "terrible" rather than "unknown"), never as a spinner
 * (the answer has arrived; it is "we cannot say yet"), and never as a
 * plausible-looking score. The API sends `overall_score: null` in that state.
 */

import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  HelpCircle,
  Loader2,
  Server,
  XCircle,
  Zap,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { SignalHealthSummary } from '@/api/dashboard';
import { missingInputLabels } from './missingInputs';

interface SignalHealthCardProps {
  signalHealth?: SignalHealthSummary;
  loading?: boolean;
}

export function SignalHealthCard({ signalHealth, loading = false }: SignalHealthCardProps) {
  const { t } = useTranslation();

  if (loading) {
    return (
      <div className="glass border border-white/10 rounded-xl p-5 h-full flex items-center justify-center min-h-[200px]">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!signalHealth) {
    return (
      <div className="glass border border-white/10 rounded-xl p-5 h-full">
        <div className="text-center text-muted-foreground py-8">
          <Activity className="w-8 h-8 mx-auto mb-2 opacity-50" />
          <p>{t('signalHealth.noData')}</p>
        </div>
      </div>
    );
  }

  const insufficient = signalHealth.status === 'insufficient_data';
  const missing = missingInputLabels(signalHealth, t);

  const getStatusConfig = (status: SignalHealthSummary['status']) => {
    switch (status) {
      case 'healthy':
        return {
          color: 'text-[#00c7be]',
          bgColor: 'bg-[#00c7be]/10',
          icon: CheckCircle2,
          label: t('signalHealth.status.healthy'),
        };
      case 'degraded':
        return {
          color: 'text-yellow-500',
          bgColor: 'bg-yellow-500/10',
          icon: AlertTriangle,
          label: t('signalHealth.status.degraded'),
        };
      case 'critical':
        return {
          color: 'text-[#ff6b6b]',
          bgColor: 'bg-[#ff6b6b]/10',
          icon: XCircle,
          label: t('signalHealth.status.critical'),
        };
      default:
        // insufficient_data: deliberately neutral, not red. Unknown is not bad.
        return {
          color: 'text-muted-foreground',
          bgColor: 'bg-muted',
          icon: HelpCircle,
          label: t('signalHealth.insufficient.badge'),
        };
    }
  };

  const statusConfig = getStatusConfig(signalHealth.status);
  const StatusIcon = statusConfig.icon;

  const getScoreColor = (score: number) => {
    if (score >= 70) return 'text-[#00c7be]';
    if (score >= 40) return 'text-yellow-500';
    return 'text-[#ff6b6b]';
  };

  const formatFreshness = (minutes: number | null) => {
    if (minutes === null) return t('signalHealth.gate.notMeasured');
    if (minutes < 1) return t('signalHealth.justNow');
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h`;
    const days = Math.floor(hours / 24);
    return `${days}d`;
  };

  return (
    <div className="glass border border-white/10 rounded-xl p-5 h-full card-3d">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold">{t('signalHealth.title')}</h3>
        <div
          className={cn(
            'flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ai-chip',
            statusConfig.bgColor,
            statusConfig.color
          )}
        >
          <div
            className={cn(
              'w-1.5 h-1.5 rounded-full',
              signalHealth.status === 'healthy' && 'dot-healthy',
              signalHealth.status === 'degraded' && 'dot-degraded',
              signalHealth.status === 'critical' && 'dot-critical'
            )}
          />
          <StatusIcon className="w-3.5 h-3.5" />
          {statusConfig.label}
        </div>
      </div>

      {insufficient ? (
        /* No score, and no bar pretending to be one. */
        <div className="rounded-lg border border-dashed border-white/15 bg-muted/30 p-4 mb-4">
          <div className="flex items-start gap-2.5">
            <HelpCircle className="w-5 h-5 mt-0.5 flex-shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <p className="text-sm font-medium">{t('signalHealth.insufficient.headline')}</p>
              <p className="text-xs text-muted-foreground mt-1">
                {t('signalHealth.insufficient.body')}
              </p>
            </div>
          </div>

          {missing.length > 0 && (
            <div className="mt-3 pt-3 border-t border-white/10">
              <div className="text-xs font-medium text-muted-foreground mb-1.5">
                {t('signalHealth.insufficient.missingTitle')}
              </div>
              <ul className="space-y-1">
                {missing.map((reason) => (
                  <li key={reason} className="text-xs text-muted-foreground flex items-start gap-2">
                    <span
                      aria-hidden="true"
                      className="mt-1.5 w-1 h-1 rounded-full bg-current flex-shrink-0"
                    />
                    <span>{reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ) : (
        <div className="text-center mb-4">
          <div className={cn('text-4xl font-bold', getScoreColor(signalHealth.overall_score ?? 0))}>
            {signalHealth.overall_score}
          </div>
          <div className="text-xs text-muted-foreground mt-1">
            {t('signalHealth.overallScore')}
          </div>
          {/* Progress bar */}
          <div className="h-1.5 rounded-full bg-white/10 overflow-hidden mt-2">
            <div
              className={cn(
                'h-full rounded-full transition-all duration-500',
                (signalHealth.overall_score ?? 0) >= 70
                  ? 'bg-gradient-to-r from-[#00c7be] to-[#34c759]'
                  : (signalHealth.overall_score ?? 0) >= 40
                    ? 'bg-gradient-to-r from-yellow-500 to-amber-400'
                    : 'bg-gradient-to-r from-[#ff6b6b] to-[#ff8a8a]'
              )}
              style={{ width: `${signalHealth.overall_score ?? 0}%` }}
            />
          </div>
        </div>
      )}

      {/* Metrics. Each one shows "not measured" rather than a stand-in value. */}
      <div className="space-y-3">
        <div className="flex items-center justify-between text-sm">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Activity className="w-4 h-4" />
            <span>{t('signalHealth.emqScore')}</span>
          </div>
          {signalHealth.emq_score !== null ? (
            <span className={cn('font-medium', getScoreColor(signalHealth.emq_score))}>
              {Math.round(signalHealth.emq_score)}
            </span>
          ) : (
            <span className="font-medium text-muted-foreground">
              {t('signalHealth.gate.notMeasured')}
            </span>
          )}
        </div>

        <div className="flex items-center justify-between text-sm">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Clock className="w-4 h-4" />
            <span>{t('signalHealth.dataFreshness')}</span>
          </div>
          <span
            className={cn(
              'font-medium',
              signalHealth.data_freshness_minutes === null && 'text-muted-foreground'
            )}
          >
            {formatFreshness(signalHealth.data_freshness_minutes)}
          </span>
        </div>

        <div className="flex items-center justify-between text-sm">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Server className="w-4 h-4" />
            <span>{t('signalHealth.apiHealth')}</span>
          </div>
          <span
            className={cn(
              'font-medium',
              signalHealth.api_health === null
                ? 'text-muted-foreground'
                : signalHealth.api_health
                  ? 'text-[#00c7be]'
                  : 'text-[#ff6b6b]'
            )}
          >
            {signalHealth.api_health === null
              ? t('signalHealth.gate.notMeasured')
              : signalHealth.api_health
                ? t('signalHealth.online')
                : t('signalHealth.offline')}
          </span>
        </div>

        <div className="flex items-center justify-between text-sm">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Zap className="w-4 h-4" />
            <span>{t('signalHealth.autopilot')}</span>
          </div>
          <span
            className={cn(
              'font-medium',
              signalHealth.autopilot_enabled ? 'text-[#00c7be]' : 'text-muted-foreground'
            )}
          >
            {signalHealth.autopilot_enabled
              ? t('signalHealth.enabled')
              : t('signalHealth.disabled')}
          </span>
        </div>
      </div>

      {/* Issues. In the insufficient state the missing inputs are already
          listed above, so they are not repeated here. */}
      {!insufficient && signalHealth.issues.length > 0 && (
        <div className="mt-4 pt-4 border-t">
          <div className="text-xs font-medium text-muted-foreground mb-2">
            {t('signalHealth.issues')}
          </div>
          <ul className="space-y-1">
            {signalHealth.issues.slice(0, 3).map((issue, index) => (
              <li key={index} className="text-xs text-yellow-600 flex items-start gap-2">
                <AlertTriangle className="w-3 h-3 mt-0.5 flex-shrink-0" />
                <span>{issue}</span>
              </li>
            ))}
            {signalHealth.issues.length > 3 && (
              <li className="text-xs text-muted-foreground">
                {t('signalHealth.moreIssues', { count: signalHealth.issues.length - 3 })}
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
