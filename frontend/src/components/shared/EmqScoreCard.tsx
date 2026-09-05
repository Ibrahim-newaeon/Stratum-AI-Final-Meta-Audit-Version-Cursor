/**
 * EMQ Score Card
 * Displays the Event Measurement Quality score with gauge and drivers breakdown
 *
 * `score` and `drivers` are nullable and null means "not measured", not zero.
 * This card used to fall back to a `defaultDrivers` table - Freshness 95 /
 * Data Loss 88 / Variance 72 / Errors 98 - so any tenant whose driver
 * breakdown had never been computed was shown that invented breakdown as their
 * own, complete with progress bars and a good/warning status per driver.
 */

import { cn } from '@/lib/utils';
import { ConfidenceBandBadge, getConfidenceBand } from './ConfidenceBandBadge';
import {
  BugAntIcon,
  ChartBarIcon,
  ClockIcon,
  ExclamationCircleIcon,
} from '@heroicons/react/24/outline';

interface EmqDriver {
  name: string;
  value: number; // 0-100
  weight: number;
  status: 'good' | 'warning' | 'critical';
}

interface EmqScoreCardProps {
  /** 0-100, or null when no score has been measured for this tenant. */
  score: number | null;
  previousScore?: number | null;
  /** Measured drivers. Empty or null renders as "not measured", never as a default table. */
  drivers?: EmqDriver[] | null;
  showDrivers?: boolean;
  compact?: boolean;
  className?: string;
}

// Keyed by the names the API publishes, which are the four columns of
// fact_signal_health_daily. Anything unrecognised falls back to ChartBarIcon.
const driverIcons: Record<string, typeof ClockIcon> = {
  'Event Match Quality': ChartBarIcon,
  Freshness: ClockIcon,
  Delivery: ExclamationCircleIcon,
  'API Reliability': BugAntIcon,
};

const driverTitles: Record<string, string> = {
  'Event Match Quality': 'CAPI delivery success and hashed-identifier coverage',
  Freshness: 'Age of the newest measured data point',
  Delivery: 'Share of events that were not lost',
  'API Reliability': 'Share of platform API calls that did not error',
};

function ScoreGauge({ score, size = 120 }: { score: number | null; size?: number }) {
  const strokeWidth = 8;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;

  // An unmeasured score draws no progress arc at all. Drawing one at 0 would
  // read as a measured score of zero, which is the worst possible reading.
  const strokeDashoffset =
    score === null ? circumference : circumference - (score / 100) * circumference;

  const band = score === null ? null : getConfidenceBand(score);
  const color =
    band === 'reliable'
      ? 'var(--teal, #00c7be)'
      : band === 'directional'
        ? 'var(--status-warning, #f59e0b)'
        : band === 'unsafe'
          ? 'var(--coral, #ff6b6b)'
          : 'transparent';

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg className="transform -rotate-90" width={size} height={size}>
        {/* Background circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          className="text-white/10"
        />
        {/* Progress circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={strokeDashoffset}
          className="transition-all duration-700 ease-out"
        />
      </svg>
      {/* Center text */}
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span
          className={cn('font-bold', score === null ? 'text-2xl text-text-muted' : 'text-3xl text-white')}
        >
          {score === null ? '—' : score}
        </span>
        <span className="text-xs text-text-muted">EMQ</span>
      </div>
    </div>
  );
}

export function EmqScoreCard({
  score,
  previousScore,
  drivers,
  showDrivers = true,
  compact = false,
  className,
}: EmqScoreCardProps) {
  // Rounded: both ends are floats, so the raw difference renders as
  // "+0.10000000000000853 points from yesterday".
  const delta =
    score !== null && previousScore !== null && previousScore !== undefined
      ? Math.round((score - previousScore) * 10) / 10
      : null;

  const measuredDrivers = drivers ?? [];

  if (compact) {
    return (
      <div
        className={cn(
          'flex items-center gap-3 p-3 rounded-xl bg-surface-secondary border border-white/10',
          className
        )}
      >
        <ScoreGauge score={score} size={60} />
        <div>
          <div className="flex items-center gap-2">
            <span className="text-lg font-semibold text-white">EMQ Score</span>
            {score === null ? (
              <span className="text-xs text-text-muted">Not measured</span>
            ) : (
              <ConfidenceBandBadge score={score} size="sm" />
            )}
          </div>
          {delta !== null && (
            <span
              className={cn(
                'text-sm',
                delta > 0 ? 'text-success' : delta < 0 ? 'text-danger' : 'text-text-muted'
              )}
            >
              {delta > 0 ? '+' : ''}
              {delta} from yesterday
            </span>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={cn('p-6 rounded-2xl bg-surface-secondary border border-white/10', className)}>
      <div className="flex items-start justify-between mb-6">
        <div>
          <h3 className="text-lg font-semibold text-white">Event Measurement Quality</h3>
          <p className="text-sm text-text-muted mt-1">
            {score === null ? 'No score has been measured yet' : 'How reliable is your data today?'}
          </p>
        </div>
        {score !== null && <ConfidenceBandBadge score={score} />}
      </div>

      <div className="flex items-center gap-8">
        <ScoreGauge score={score} />

        {showDrivers &&
          (measuredDrivers.length > 0 ? (
            <div className="flex-1 space-y-3">
              {measuredDrivers.map((driver) => {
                const Icon = driverIcons[driver.name] || ChartBarIcon;
                const title = driverTitles[driver.name];
                return (
                  <div key={driver.name} className="flex items-center gap-3" title={title}>
                    <Icon
                      className={cn(
                        'w-4 h-4',
                        driver.status === 'good'
                          ? 'text-success'
                          : driver.status === 'warning'
                            ? 'text-warning'
                            : 'text-danger'
                      )}
                    />
                    <div className="flex-1">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-sm text-text-secondary" title={title}>
                          {driver.name}
                        </span>
                        <span
                          className={cn(
                            'text-sm font-medium',
                            driver.status === 'good'
                              ? 'text-success'
                              : driver.status === 'warning'
                                ? 'text-warning'
                                : 'text-danger'
                          )}
                        >
                          {driver.value}%
                        </span>
                      </div>
                      <div className="h-1.5 bg-white/10 rounded-full overflow-hidden">
                        <div
                          className={cn(
                            'h-full rounded-full transition-all duration-500',
                            driver.status === 'good'
                              ? 'bg-success'
                              : driver.status === 'warning'
                                ? 'bg-warning'
                                : 'bg-danger'
                          )}
                          style={{ width: `${driver.value}%` }}
                        />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="flex-1 text-sm text-text-muted">
              Driver breakdown not measured for this tenant.
            </div>
          ))}
      </div>

      {delta !== null && (
        <div className="mt-4 pt-4 border-t border-white/10">
          <span
            className={cn(
              'text-sm',
              delta > 0 ? 'text-success' : delta < 0 ? 'text-danger' : 'text-text-muted'
            )}
          >
            {delta > 0 ? '+' : ''}
            {delta} points from yesterday
          </span>
        </div>
      )}
    </div>
  );
}

export default EmqScoreCard;
