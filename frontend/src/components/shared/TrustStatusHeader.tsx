/**
 * Trust Status Header
 * Universal header component showing EMQ + band + mode + budget-at-risk
 * Used on ALL dashboard pages
 *
 * Every displayed value is nullable and null means "not measured". A tenant
 * with no signal health is neither trustworthy nor untrustworthy, so the
 * "Can I trust today?" answer for an unmeasured score is that nothing has been
 * measured - never "No, fix data issues first", which is a verdict, and never
 * a number.
 */

import { cn } from '@/lib/utils';
import { ConfidenceBandBadge, getConfidenceBand } from './ConfidenceBandBadge';
import { type AutopilotMode, AutopilotModeBanner } from './AutopilotModeBanner';
import { BudgetAtRiskChip } from './BudgetAtRiskChip';
import { VolatilityBadge } from './VolatilityBadge';
import {
  ChevronRightIcon,
  QuestionMarkCircleIcon,
  ShieldCheckIcon,
  ShieldExclamationIcon,
} from '@heroicons/react/24/outline';

interface TrustStatusHeaderProps {
  /** 0-100, or null when no score has been measured for this tenant. */
  emqScore: number | null;
  /** Null when no autopilot state has been resolved. */
  autopilotMode: AutopilotMode | null;
  /** Null when budget at risk has not been measured; 0 is a real measurement. */
  budgetAtRisk: number | null;
  svi?: number | null; // Signal Volatility Index
  currency?: string;
  onViewDetails?: () => void;
  compact?: boolean;
  className?: string;
}

function getEmqStatus(score: number | null) {
  if (score === null) {
    return {
      icon: QuestionMarkCircleIcon,
      color: 'text-text-muted',
      bgColor: 'bg-white/5',
      message: 'No signal health has been measured for this tenant',
      answer: 'Not measured',
    };
  }
  const band = getConfidenceBand(score);
  if (band === 'reliable') {
    return {
      icon: ShieldCheckIcon,
      color: 'text-success',
      bgColor: 'bg-success/10',
      message: 'Data is trustworthy for decision-making',
      answer: score >= 90 ? 'Yes, data is reliable' : 'Partially, use with caution',
    };
  }
  if (band === 'directional') {
    return {
      icon: ShieldExclamationIcon,
      color: 'text-warning',
      bgColor: 'bg-warning/10',
      message: 'Data shows trends but may have gaps',
      answer: score >= 60 ? 'Partially, use with caution' : 'No, fix data issues first',
    };
  }
  return {
    icon: ShieldExclamationIcon,
    color: 'text-danger',
    bgColor: 'bg-danger/10',
    message: 'Data quality too low for reliable decisions',
    answer: 'No, fix data issues first',
  };
}

export function TrustStatusHeader({
  emqScore,
  autopilotMode,
  budgetAtRisk,
  svi,
  currency = 'USD',
  onViewDetails,
  compact = false,
  className,
}: TrustStatusHeaderProps) {
  const status = getEmqStatus(emqScore);
  const StatusIcon = status.icon;
  const showBudget = budgetAtRisk !== null && budgetAtRisk > 0;

  if (compact) {
    return (
      <div
        className={cn(
          'flex items-center gap-4 p-3 rounded-xl bg-surface-secondary border border-white/10',
          className
        )}
      >
        <div className="flex items-center gap-2">
          <StatusIcon className={cn('w-5 h-5', status.color)} />
          {emqScore === null ? (
            <span className="text-sm text-text-muted">EMQ not measured</span>
          ) : (
            <>
              <span className="text-lg font-bold text-white">{emqScore}</span>
              <ConfidenceBandBadge score={emqScore} size="sm" />
            </>
          )}
        </div>
        {autopilotMode !== null && (
          <>
            <div className="h-4 w-px bg-white/10" />
            <AutopilotModeBanner mode={autopilotMode} compact />
          </>
        )}
        {showBudget && (
          <>
            <div className="h-4 w-px bg-white/10" />
            <BudgetAtRiskChip amount={budgetAtRisk} currency={currency} size="sm" />
          </>
        )}
        {svi !== undefined && svi !== null && (
          <>
            <div className="h-4 w-px bg-white/10" />
            <VolatilityBadge svi={svi} size="sm" />
          </>
        )}
      </div>
    );
  }

  return (
    <div
      className={cn(
        'rounded-2xl bg-surface-secondary border border-white/10 overflow-hidden',
        className
      )}
    >
      {/* Main header */}
      <div className="p-6">
        <div className="flex items-start justify-between">
          <div className="flex items-start gap-4">
            <div className={cn('p-3 rounded-xl', status.bgColor)}>
              <StatusIcon className={cn('w-6 h-6', status.color)} />
            </div>
            <div>
              <div className="flex items-center gap-3">
                <h2 className="text-2xl font-bold text-white">
                  {emqScore === null ? 'EMQ —' : `EMQ ${emqScore}`}
                </h2>
                {emqScore !== null && <ConfidenceBandBadge score={emqScore} />}
              </div>
              <p className="text-sm text-text-secondary mt-1">{status.message}</p>
            </div>
          </div>

          {onViewDetails && (
            <button
              onClick={onViewDetails}
              className="flex items-center gap-1 text-sm text-stratum-400 hover:text-stratum-300 transition-colors"
            >
              View details
              <ChevronRightIcon className="w-4 h-4" />
            </button>
          )}
        </div>

        {/* Stats row */}
        <div className="flex items-center gap-6 mt-6 pt-6 border-t border-white/10">
          <div className="flex-1">
            {autopilotMode === null ? (
              <span className="text-sm text-text-muted">Autopilot mode not measured</span>
            ) : (
              <AutopilotModeBanner mode={autopilotMode} showDescription={false} compact />
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm text-text-muted">Budget at Risk:</span>
            {budgetAtRisk === null ? (
              <span className="text-sm text-text-muted">Not measured</span>
            ) : (
              <BudgetAtRiskChip amount={budgetAtRisk} currency={currency} />
            )}
          </div>
          {svi !== undefined && svi !== null && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-text-muted">Volatility:</span>
              <VolatilityBadge svi={svi} />
            </div>
          )}
        </div>
      </div>

      {/* "Can I trust today?" quick answer */}
      <div className={cn('px-6 py-4 border-t', status.bgColor, 'border-white/10')}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="font-medium text-white">Can I trust today?</span>
            <span className={cn('font-semibold', status.color)}>{status.answer}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default TrustStatusHeader;
