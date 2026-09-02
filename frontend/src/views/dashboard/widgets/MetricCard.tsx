/**
 * MetricCard - Compact KPI metric display for the unified dashboard.
 */

import type { ReactNode } from 'react';
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { TrendDirection } from '@/api/dashboard';

export interface MetricCardProps {
  title: string;
  value: string | number;
  /** Percent change vs previous period */
  change?: number | null;
  trend?: TrendDirection;
  icon?: ReactNode;
  loading?: boolean;
  /** When true, a positive change is rendered green (revenue-like metrics) */
  positive?: boolean;
  /** Visually emphasize the card (e.g. ROAS above target) */
  highlight?: boolean;
  size?: 'small' | 'default';
}

export function MetricCard({
  title,
  value,
  change,
  trend,
  icon,
  loading = false,
  positive = false,
  highlight = false,
  size = 'default',
}: MetricCardProps) {
  const isSmall = size === 'small';

  const changeIsGood =
    change !== undefined && change !== null
      ? positive
        ? change >= 0
        : change <= 0
      : undefined;

  const TrendIcon =
    trend === 'up' ? ArrowUpRight : trend === 'down' ? ArrowDownRight : Minus;

  return (
    <div
      className={cn(
        'rounded-xl border bg-card transition-all hover:shadow-md',
        isSmall ? 'p-3' : 'p-4',
        highlight && 'border-primary/50 ring-1 ring-primary/20'
      )}
    >
      <div className="flex items-center justify-between mb-2">
        <span
          className={cn(
            'font-medium text-muted-foreground',
            isSmall ? 'text-xs' : 'text-sm'
          )}
        >
          {title}
        </span>
        {icon && <span className="text-muted-foreground">{icon}</span>}
      </div>

      {loading ? (
        <div
          className={cn('bg-muted animate-pulse rounded', isSmall ? 'h-6 w-16' : 'h-8 w-24')}
        />
      ) : (
        <>
          <div className={cn('font-bold', isSmall ? 'text-lg' : 'text-2xl')}>{value}</div>
          {change !== undefined && change !== null && (
            <div className="flex items-center gap-1 mt-1">
              <TrendIcon
                className={cn(
                  'w-3.5 h-3.5',
                  changeIsGood ? 'text-green-500' : 'text-red-500'
                )}
              />
              <span
                className={cn(
                  'text-xs font-medium',
                  changeIsGood ? 'text-green-500' : 'text-red-500'
                )}
              >
                {change >= 0 ? '+' : ''}
                {change.toFixed(1)}%
              </span>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default MetricCard;
