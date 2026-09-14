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
      style={{
        borderColor: 'var(--studio-border)',
        background: 'var(--studio-nav-active)',
      }}
    >
      <div className="mx-auto flex max-w-[1200px] items-center justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" style={{ color: 'var(--studio-accent)' }} />
          <div className="min-w-0">
            <p className="text-sm font-medium" style={{ color: 'var(--studio-text)' }}>
              Meta setup incomplete ({status.required_done}/{status.required_total} required steps)
            </p>
            <p className="text-xs" style={{ color: 'var(--studio-text-secondary)' }}>
              Connect OAuth, CAPI, and your Marketing API System User token so audiences and
              campaigns work together.
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Link to="/dashboard/activation" className="studio-btn-primary text-xs">
            Complete setup
          </Link>
          <button
            type="button"
            onClick={() => setDismissed(true)}
            className="rounded p-1"
            style={{ color: 'var(--studio-text-secondary)' }}
            aria-label="Dismiss banner"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
