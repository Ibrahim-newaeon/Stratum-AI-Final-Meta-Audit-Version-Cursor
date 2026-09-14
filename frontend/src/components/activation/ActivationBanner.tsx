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
      className="relative border-b border-amber-500/30 bg-amber-500/10 px-4 py-3"
    >
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-amber-100">
              Meta setup incomplete ({status.required_done}/{status.required_total} required steps)
            </p>
            <p className="text-xs text-amber-200/70">
              Connect OAuth, CAPI, and your Marketing API System User token so audiences and
              campaigns work together — not just Facebook login.
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Link
            to="/dashboard/activation"
            className="rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-medium text-black hover:bg-amber-400"
          >
            Complete setup
          </Link>
          <button
            type="button"
            onClick={() => setDismissed(true)}
            className="rounded p-1 text-amber-200/60 hover:bg-amber-500/20 hover:text-amber-100"
            aria-label="Dismiss banner"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
