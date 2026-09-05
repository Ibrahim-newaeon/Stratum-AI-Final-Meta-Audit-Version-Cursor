/**
 * Trust Gate Indicator Component
 *
 * The fixed corner badge shown on every authenticated dashboard route. It
 * reports what the backend trust gate has actually decided for this tenant.
 *
 * It used to seed `useState(85)` from a comment reading "Simulated signal
 * health - in production this would come from the trust engine API" and
 * random-walk it every five seconds, mapping the result to PASS through its
 * own copies of the 70/40 thresholds. A brand-new tenant therefore saw a
 * glowing green "85 PASS" badge in the corner while the signal health card on
 * the same screen correctly said there was not enough data to score - and
 * while the real gate was blocking every automation.
 *
 * The state now comes from `signal_health.gate_decision`, which the API takes
 * from the gate itself rather than re-deriving from a score, so this badge
 * cannot promise an automation the gate will refuse. When the gate has nothing
 * to grade there is no number to show: the badge reads NO DATA.
 */

import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { useDashboardSignalHealth } from '@/api/dashboard';
import { cn } from '@/lib/utils';

interface TrustGateIndicatorProps {
  className?: string;
}

type TrustStatus = 'PASS' | 'HOLD' | 'BLOCK' | 'UNKNOWN';

interface TrustState {
  status: TrustStatus;
  color: string;
  bgColor: string;
}

/**
 * Read a themed colour, falling back to the literal when the variable is unset.
 */
function themeColor(variable: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(variable).trim();
  return value || fallback;
}

/**
 * Map the gate's own decision onto the badge's presentation.
 *
 * `gate_decision` is authoritative. The thresholds live in backend
 * configuration (and may be overridden per tenant during onboarding), so
 * re-deriving the state from the score here would drift from what is enforced.
 */
function getTrustState(
  gateDecision: string | null | undefined,
  hasSnapshot: boolean
): TrustState {
  switch (gateDecision) {
    case 'pass':
      return {
        status: 'PASS',
        color: themeColor('--teal', '#00c7be'),
        bgColor: themeColor('--teal-light', 'rgba(0, 199, 190, 0.15)'),
      };
    case 'hold':
      return {
        status: 'HOLD',
        color: themeColor('--status-warning', '#f59e0b'),
        bgColor: themeColor('--status-warning-bg', 'rgba(245, 158, 11, 0.15)'),
      };
    case 'block':
      // The gate blocks for two different reasons: a bad measurement, and no
      // measurement at all. Without a snapshot to grade it is the second, and
      // BLOCK would tell the tenant their signal is critical when in truth
      // nobody has measured it - which is also what the signal health card on
      // the same screen says. Both must agree.
      if (!hasSnapshot) break;
      return {
        status: 'BLOCK',
        color: themeColor('--coral', '#ff6b6b'),
        bgColor: 'rgba(255, 107, 107, 0.15)',
      };
    default:
      break;
  }
  // No decision yet (still loading, or the request failed), or nothing for the
  // gate to grade. Unknown is not a passing grade, so it never renders as one -
  // and it is not a failing one either.
  return {
    status: 'UNKNOWN',
    color: 'rgba(255, 255, 255, 0.55)',
    bgColor: 'rgba(255, 255, 255, 0.08)',
  };
}

export const TrustGateIndicator = memo(function TrustGateIndicator({
  className,
}: TrustGateIndicatorProps) {
  const { t } = useTranslation();
  const { data: signalHealth } = useDashboardSignalHealth();

  const state = getTrustState(
    signalHealth?.gate_decision,
    Boolean(signalHealth?.gate_health_date)
  );
  const isHealthy = state.status === 'PASS';
  // A score is shown only when one was measured. `insufficient_data` sends
  // null, and rendering that as 0 would read as "terrible" rather than
  // "unknown".
  const score = signalHealth?.overall_score ?? null;
  const statusLabel = t(`signalHealth.gate.${state.status.toLowerCase()}`, {
    defaultValue: state.status,
  });

  return (
    <div
      className={cn(
        'fixed bottom-6 left-6 z-50',
        'flex items-center gap-3 px-4 py-3 rounded-2xl',
        'transition-all duration-300',
        className
      )}
      style={{
        background: 'var(--bg-primary)',
        border: isHealthy ? '2px solid var(--teal)' : '2px solid rgba(255, 255, 255, 0.15)',
        boxShadow: isHealthy
          ? '0 0 20px var(--teal-glow), 0 8px 32px rgba(0, 0, 0, 0.3)'
          : '0 8px 32px rgba(0, 0, 0, 0.3)',
      }}
      role="status"
      aria-label={
        score === null
          ? `${t('signalHealth.gate.title')}: ${statusLabel}, ${t('signalHealth.gate.notMeasured')}`
          : `${t('signalHealth.gate.title')}: ${statusLabel}, ${t('signalHealth.title')}: ${score}%`
      }
    >
      {/* Status dot */}
      <div className="relative flex items-center justify-center">
        <div
          className={cn('h-3 w-3 rounded-full', isHealthy && 'animate-status-pulse')}
          style={{
            backgroundColor: state.color,
            boxShadow: isHealthy ? `0 0 12px ${state.color}` : 'none',
          }}
        />
        {isHealthy && (
          <div
            className="absolute inset-0 h-3 w-3 rounded-full animate-ping"
            style={{
              backgroundColor: state.color,
              opacity: 0.4,
              animationDuration: '2s',
            }}
          />
        )}
      </div>

      {/* Status info */}
      <div className="flex flex-col">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold font-mono" style={{ color: state.color }}>
            {score === null ? '—' : score}
          </span>
          <span
            className="text-xs font-medium px-1.5 py-0.5 rounded"
            style={{
              backgroundColor: state.bgColor,
              color: state.color,
            }}
          >
            {statusLabel}
          </span>
        </div>
        <span className="text-xs" style={{ color: 'rgba(255, 255, 255, 0.5)' }}>
          {t('signalHealth.title')}
        </span>
      </div>
    </div>
  );
});

export default TrustGateIndicator;
