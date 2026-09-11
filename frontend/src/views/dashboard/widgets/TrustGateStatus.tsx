/**
 * TrustGateStatus - Visual trust gate indicator
 *
 * Mirrors the backend gate:
 * - PASS (green): signal health healthy, autopilot executes
 * - HOLD (yellow): signal health degraded, alerts only
 * - BLOCK (red): signal health critical, manual required
 * - NO DATA (neutral): signal health could not be measured
 *
 * The gate state is taken from `signalHealth.gate_decision`, which the API
 * takes from the trust gate itself. Two earlier versions of this were wrong in
 * the same direction: first it re-derived the state from the score against its
 * own copies of 70/40 (so a deployment that tuned the thresholds showed one
 * thing and enforced another), then it read `status`, which bands a *live*
 * trailing-window measurement while the gate grades yesterday's persisted
 * snapshot. A tenant who delivered ten events this morning was shown
 * "PASS - autopilot executes" while every automation was in fact being
 * blocked for want of a snapshot. The numeric thresholds below are kept only
 * to draw the band markers on the progress bar.
 *
 * `insufficient_data` is rendered as its own neutral state. It is not BLOCK:
 * nothing is wrong, nothing is known yet. Showing 0/100 there would claim a
 * measurement that was never taken.
 */

import {
  AlertTriangle,
  Hand,
  HelpCircle,
  Hourglass,
  Loader2,
  ShieldAlert,
  ShieldCheck,
  ShieldOff,
  Zap,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { SignalHealthSummary } from '@/api/dashboard';
import { missingInputLabels } from './missingInputs';

// Band markers for the progress bar only - the gate decision itself comes from
// the API, which reads the thresholds from configuration. Prefer the tenant's
// reported thresholds so a tuned deployment does not draw 70/40 while the gate
// enforces something else.
const DEFAULT_HEALTHY_BAND = 70;
const DEFAULT_DEGRADED_BAND = 40;

type GateStatus = 'PASS' | 'HOLD' | 'BLOCK' | 'UNKNOWN';

interface TrustGateStatusProps {
  signalHealth?: SignalHealthSummary;
  loading?: boolean;
}

export function TrustGateStatus({ signalHealth, loading = false }: TrustGateStatusProps) {
  const { t } = useTranslation();
  const healthyBand = signalHealth?.healthy_threshold ?? DEFAULT_HEALTHY_BAND;
  const degradedBand = signalHealth?.degraded_threshold ?? DEFAULT_DEGRADED_BAND;

  if (loading) {
    return (
      <div className="glass border border-white/10 rounded-xl p-6 h-full flex items-center justify-center min-h-[200px]">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const getGateStatus = (): GateStatus => {
    // No response at all is still an unknown, and the backend gate fails
    // closed on it - nothing executes either way.
    if (!signalHealth) return 'UNKNOWN';
    switch (signalHealth.gate_decision) {
      case 'pass':
        return 'PASS';
      case 'hold':
        return 'HOLD';
      case 'block':
        // The gate blocks for two different reasons: a bad measurement, and no
        // measurement at all. `gate_health_date === null` is exactly the
        // second, and rendering it as BLOCK would tell a tenant their signal is
        // critical when in truth nobody has measured it.
        return signalHealth.gate_health_date === null ? 'UNKNOWN' : 'BLOCK';
      default:
        return 'UNKNOWN';
    }
  };

  const gateStatus = getGateStatus();
  const score = signalHealth?.overall_score ?? null;
  const missing = signalHealth ? missingInputLabels(signalHealth, t) : [];

  const gateConfigs = {
    PASS: {
      icon: ShieldCheck,
      color: 'text-[#00c7be]',
      bgColor: 'bg-[#00c7be]',
      bgLight: 'bg-[#00c7be]/10',
      borderColor: 'border-[#00c7be]/30',
      ringColor: 'ring-[#00c7be]/20',
      label: t('signalHealth.gate.pass'),
      description: t('signalHealth.gate.passDescription'),
      detail: t('signalHealth.gate.passDetail'),
      actionIcon: Zap,
      actionLabel: t('signalHealth.gate.autoExecute'),
    },
    HOLD: {
      icon: ShieldAlert,
      color: 'text-yellow-500',
      bgColor: 'bg-yellow-500',
      bgLight: 'bg-yellow-500/10',
      borderColor: 'border-yellow-500/30',
      ringColor: 'ring-yellow-500/20',
      label: t('signalHealth.gate.hold'),
      description: t('signalHealth.gate.holdDescription'),
      detail: t('signalHealth.gate.holdDetail'),
      actionIcon: AlertTriangle,
      actionLabel: t('signalHealth.gate.alertOnly'),
    },
    BLOCK: {
      icon: ShieldOff,
      color: 'text-[#ff6b6b]',
      bgColor: 'bg-[#ff6b6b]',
      bgLight: 'bg-[#ff6b6b]/10',
      borderColor: 'border-[#ff6b6b]/30',
      ringColor: 'ring-[#ff6b6b]/20',
      label: t('signalHealth.gate.block'),
      description: t('signalHealth.gate.blockDescription'),
      detail: t('signalHealth.gate.blockDetail'),
      actionIcon: Hand,
      actionLabel: t('signalHealth.gate.manualOnly'),
    },
    UNKNOWN: {
      icon: HelpCircle,
      color: 'text-muted-foreground',
      bgColor: 'bg-muted-foreground',
      bgLight: 'bg-muted',
      borderColor: 'border-white/15',
      ringColor: 'ring-white/10',
      label: t('signalHealth.gate.unknown'),
      description: t('signalHealth.gate.unknownDescription'),
      detail: t('signalHealth.gate.unknownDetail'),
      actionIcon: Hourglass,
      actionLabel: t('signalHealth.gate.waitingForData'),
    },
  };

  const config = gateConfigs[gateStatus];
  const GateIcon = config.icon;
  const ActionIcon = config.actionIcon;

  return (
    <div
      className={cn(
        'glass border border-white/10 rounded-xl p-6 h-full card-3d',
        config.borderColor
      )}
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold">{t('signalHealth.gate.title')}</h3>
        <div
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-bold ai-chip',
            config.bgLight,
            config.color
          )}
        >
          <GateIcon className="w-4 h-4" />
          {config.label}
        </div>
      </div>

      {/* Visual Gate Indicator */}
      <div className="flex items-center justify-center py-6">
        <div className="relative">
          {/* Outer ring with glow */}
          <div
            className={cn(
              'w-32 h-32 rounded-full ring-4 flex items-center justify-center',
              config.ringColor,
              config.bgLight,
              gateStatus === 'PASS' && 'animate-glow-loop',
              gateStatus === 'UNKNOWN' && 'border border-dashed border-white/20'
            )}
          >
            {/* Inner circle with icon */}
            <div
              className={cn(
                'w-20 h-20 rounded-full flex items-center justify-center',
                config.bgLight
              )}
            >
              <GateIcon className={cn('w-10 h-10', config.color)} />
            </div>
          </div>

          {/* Pulsing effect for PASS status */}
          {gateStatus === 'PASS' && (
            <div
              className={cn(
                'absolute inset-0 w-32 h-32 rounded-full animate-ping opacity-20',
                config.bgColor
              )}
            />
          )}
        </div>
      </div>

      {/* Status Description */}
      <div className="text-center mb-6">
        <div className={cn('text-lg font-semibold mb-1', config.color)}>{config.description}</div>
        <p className="text-sm text-muted-foreground">{config.detail}</p>
      </div>

      {gateStatus === 'UNKNOWN' || score === null ? (
        /* No score, so no gauge. The missing inputs are the useful content. */
        <div className="rounded-lg border border-dashed border-white/15 bg-muted/30 p-3 space-y-2">
          <div className="text-xs font-medium text-muted-foreground">
            {t('signalHealth.insufficient.missingTitle')}
          </div>
          {missing.length > 0 ? (
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
          ) : (
            <p className="text-xs text-muted-foreground">{t('signalHealth.insufficient.body')}</p>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">{t('signalHealth.gate.signalScore')}</span>
            <span className={cn('font-bold', config.color)}>{score}/100</span>
          </div>

          {/* Progress bar */}
          <div className="h-2 bg-muted rounded-full overflow-hidden">
            <div className="h-full flex">
              <div
                className="bg-[#ff6b6b] transition-all duration-500"
                style={{ width: `${Math.min(degradedBand, score ?? 0)}%` }}
              />
              <div
                className="bg-yellow-500 transition-all duration-500"
                style={{
                  width: `${Math.max(0, Math.min(healthyBand - degradedBand, (score ?? 0) - degradedBand))}%`,
                }}
              />
              <div
                className="bg-[#00c7be] transition-all duration-500"
                style={{ width: `${Math.max(0, (score ?? 0) - healthyBand)}%` }}
              />
            </div>
          </div>

          {/* Threshold markers */}
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>0</span>
            <span className="text-[#ff6b6b]">
              {t('signalHealth.gate.block')} (&lt;{degradedBand})
            </span>
            <span className="text-yellow-500">
              {t('signalHealth.gate.hold')} ({degradedBand}-{healthyBand - 1})
            </span>
            <span className="text-[#00c7be]">
              {t('signalHealth.gate.pass')} (&gt;={healthyBand})
            </span>
          </div>
        </div>
      )}

      {/* Action Mode */}
      <div
        className={cn('mt-6 pt-4 border-t flex items-center justify-between', config.borderColor)}
      >
        <div className="flex items-center gap-2">
          <ActionIcon className={cn('w-4 h-4', config.color)} />
          <span className="text-sm font-medium">{config.actionLabel}</span>
        </div>
        {signalHealth?.autopilot_enabled && gateStatus === 'PASS' && (
          <span className="text-xs px-2 py-1 bg-[#00c7be]/10 text-[#00c7be] rounded-full font-medium">
            {t('signalHealth.enabled')}
          </span>
        )}
      </div>
    </div>
  );
}
