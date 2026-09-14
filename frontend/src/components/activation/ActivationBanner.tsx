/**
 * Persistent banner when required Meta integrations are incomplete.
 */

import { Link } from 'react-router-dom';
import { AlertTriangle, X } from 'lucide-react';
import { useState } from 'react';
import { useActivationStatus } from '@/api/activation';

export function ActivationBanner() {
  const { data: status } = useActivationStatus();
  const [dismissed, setDismissed] = useState(false);

  if (dismissed || !status || status.required_complete) {
    return null;
  }

  return (
    <div
      role="alert"
      className="relative border-b px-4 py-3"
      style={{ borderColor: 'var(--er-border)', background: 'var(--er-surface-2)' }}
    >
      <div className="mx-auto flex max-w-[1200px] items-center justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" style={{ color: 'var(--er-accent)' }} />
          <div className="min-w-0">
            <p className="text-sm font-medium" style={{ color: 'var(--er-text)' }}>
              Meta setup incomplete ({status.required_done}/{status.required_total} required)
            </p>
            <p className="text-xs" style={{ color: 'var(--er-muted)' }}>
              OAuth, CAPI, and Marketing API System User token are required before audiences and
              campaigns integrate properly.
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Link to="/dashboard/activation" className="er-btn-primary !py-2 !px-3 text-xs">
            Complete setup
          </Link>
          <button
            type="button"
            onClick={() => setDismissed(true)}
            className="p-1"
            style={{ color: 'var(--er-muted)' }}
            aria-label="Dismiss banner"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
