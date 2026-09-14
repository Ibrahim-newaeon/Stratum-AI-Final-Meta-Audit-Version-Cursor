/**
 * Overview — The Front Desk
 * Triage → Inspect → Authorize. Trust Hold row expands into an Evidence Room dossier.
 */

import { Fragment, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useActivationStatus } from '@/api/activation';
import { SAMPLE_LABEL } from '@/theme/evidence';
import { useAuth } from '@/contexts/AuthContext';

type HoldStatus = 'held' | 'resolved';

interface TrustHold {
  id: string;
  campaign: string;
  violation: string;
  detected: string;
  intendedAction: string;
  evidencePoints: { key: string; value: string }[];
  status: HoldStatus;
  resolvedBy?: string;
}

const INITIAL_HOLDS: TrustHold[] = [
  {
    id: 'DEC-2026-0914-0042',
    campaign: 'Spring Acquisition / Search',
    violation: 'Conversion tracking anomaly',
    detected: '14 Sep 2026, 09:31 UTC',
    intendedAction: 'Increase daily budget by 18%',
    evidencePoints: [
      { key: 'signal.health', value: 'DEGRADED' },
      { key: 'conversion.lag_hours', value: '11' },
      { key: 'tracking.status', value: 'ANOMALY_DETECTED' },
      { key: 'pacing.band', value: 'WITHIN_POLICY' },
      { key: 'trust_gate', value: 'HOLD' },
    ],
    status: 'held',
  },
  {
    id: 'DEC-2026-0914-0039',
    campaign: 'Retargeting — Cart Abandon',
    violation: 'Signal freshness window exceeded',
    detected: '14 Sep 2026, 08:05 UTC',
    intendedAction: 'Pause ad set ADSET-8841',
    evidencePoints: [
      { key: 'signal.source', value: 'web_conversion_events' },
      { key: 'last_event_at', value: '13 Sep 2026, 21:02 UTC' },
      { key: 'freshness_threshold_h', value: '6' },
      { key: 'trust_gate', value: 'HOLD' },
    ],
    status: 'held',
  },
];

/** Honest unavailable metrics — do not mask undefined backend paths */
const KPI = [
  {
    title: 'Trust Gate',
    value: '1 HOLD',
    detail: 'Automations waiting for manual review',
    tone: 'critical' as const,
    available: true,
  },
  {
    title: 'Signal Health',
    value: 'DEGRADED',
    detail: '2 sources require review',
    tone: 'warn' as const,
    available: true,
  },
  {
    title: 'ROAS Today',
    value: undefined,
    detail: '["roas","today",null] data is undefined',
    tone: 'muted' as const,
    available: false,
  },
  {
    title: 'Pacing Today',
    value: undefined,
    detail: '["pacing","summary",null] data is undefined',
    tone: 'muted' as const,
    available: false,
  },
];

export default function EvidenceOverview() {
  const { user } = useAuth();
  const { data: activation } = useActivationStatus();
  const [holds, setHolds] = useState(INITIAL_HOLDS);
  const [openId, setOpenId] = useState<string | null>(INITIAL_HOLDS[0]?.id ?? null);
  const [ackNote, setAckNote] = useState<string | null>(null);

  const openHolds = holds.filter((h) => h.status === 'held');
  const actor = user?.name || user?.email || 'Operator';

  const resolveHold = (id: string, action: 'authorize' | 'keep' | 'adjust') => {
    setHolds((prev) =>
      prev.map((h) =>
        h.id === id
          ? {
              ...h,
              status: action === 'keep' ? 'held' : 'resolved',
              resolvedBy: action === 'keep' ? undefined : actor,
              violation:
                action === 'authorize'
                  ? 'Override authorized'
                  : action === 'adjust'
                    ? 'Parameters adjusted — re-queued'
                    : h.violation,
            }
          : h
      )
    );
    if (action !== 'keep') {
      setOpenId(null);
      setAckNote(
        action === 'authorize'
          ? `Override logged for ${id} by ${actor}`
          : `Parameters adjusted for ${id} by ${actor}`
      );
    }
  };

  const acknowledgeAll = () => {
    setAckNote(`Acknowledged ${openHolds.length} attention item(s) — ${actor}`);
  };

  return (
    <div className="mx-auto max-w-[1200px] space-y-6 px-6 py-6">
      {/* Getting started */}
      <div className="er-panel px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="er-label">Getting started</p>
            <p className="mt-0.5 text-xs" style={{ color: 'var(--er-muted)' }}>
              {activation?.required_done ?? 0} / {activation?.required_total ?? 3} Meta steps —
              OAuth, CAPI, Marketing API token
            </p>
            <div className="mt-2 h-px w-full" style={{ background: 'var(--er-border)' }}>
              <div
                className="h-px"
                style={{
                  width: `${activation?.progress_percent ?? 0}%`,
                  background: 'var(--er-accent)',
                }}
              />
            </div>
          </div>
          <Link to="/dashboard/activation" className="er-btn-primary !px-3 !py-2 text-xs">
            Resume setup
          </Link>
        </div>
      </div>

      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="er-serif text-3xl md:text-4xl">Overview</h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--er-muted)' }}>
            What needs your attention right now.
          </p>
        </div>
        <div className="text-right">
          <p className="er-mono text-[10px]" style={{ color: 'var(--er-muted)' }}>
            Data refreshed 14 Sep 2026 at 09:42 UTC
          </p>
          <p className="er-mono text-[10px]" style={{ color: 'var(--er-muted)' }}>
            {SAMPLE_LABEL}
          </p>
        </div>
      </div>

      {/* KPI row */}
      <section
        className="grid grid-cols-1 gap-px border sm:grid-cols-2 lg:grid-cols-4"
        style={{ background: 'var(--er-border)', borderColor: 'var(--er-border)' }}
        aria-label="Core health measures"
      >
        {KPI.map((k) => (
          <div key={k.title} className="p-4" style={{ background: 'var(--er-surface)' }}>
            <p className="er-label">{k.title}</p>
            {k.available ? (
              <p
                className={`er-mono mt-3 text-sm font-medium ${
                  k.tone === 'critical'
                    ? 'er-status-critical'
                    : k.tone === 'warn'
                      ? 'er-status-warn'
                      : 'er-status-pass'
                }`}
              >
                {k.value}
              </p>
            ) : (
              <p className="er-mono mt-3 text-xs leading-5" style={{ color: 'var(--er-critical)' }}>
                undefined
              </p>
            )}
            <p
              className="mt-2 text-xs leading-5"
              style={{
                color: k.available ? 'var(--er-muted)' : 'var(--er-critical)',
                fontFamily: k.available ? 'inherit' : 'var(--er-font-mono)',
              }}
            >
              {k.detail}
            </p>
          </div>
        ))}
      </section>

      {/* Triage queue */}
      <section
        className="er-panel flex flex-wrap items-center justify-between gap-3 px-4 py-3"
        aria-label="Triage queue"
      >
        <div>
          <p className="er-label mb-2">Triage</p>
          <div className="flex flex-wrap gap-4 text-sm">
            <a href="#trust-holds" className="no-underline" style={{ color: 'var(--er-text)' }}>
              <span className="er-mono font-medium" style={{ color: 'var(--er-accent)' }}>
                {openHolds.length}
              </span>{' '}
              Trust holds
            </a>
            <span>
              <span className="er-mono font-medium">1</span> Signal drops
            </span>
            <span>
              <span className="er-mono font-medium">1</span> Autopilot pending
            </span>
          </div>
        </div>
        <div className="flex gap-2">
          <a href="#trust-holds" className="er-btn-primary !px-3 !py-2 text-xs">
            Review
          </a>
          <button type="button" className="er-btn-ghost !px-3 !py-2 text-xs" onClick={acknowledgeAll}>
            Acknowledge all
          </button>
        </div>
      </section>

      {ackNote && (
        <p className="er-mono text-xs" style={{ color: 'var(--er-pass)' }} role="status">
          {ackNote}
        </p>
      )}

      {/* Trust holds ledger */}
      <section id="trust-holds" className="er-panel scroll-mt-6" aria-labelledby="holds-heading">
        <div className="border-b px-4 py-3" style={{ borderColor: 'var(--er-border)' }}>
          <h2 id="holds-heading" className="text-sm font-medium">
            Trust holds
          </h2>
          <p className="mt-1 text-xs" style={{ color: 'var(--er-muted)' }}>
            Automations the trust gate is holding for manual review.
          </p>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="er-label" style={{ color: 'var(--er-muted)' }}>
                <th className="w-8 px-4 py-3 font-medium" />
                <th className="px-4 py-3 font-medium">Campaign</th>
                <th className="px-4 py-3 font-medium">Violation</th>
                <th className="px-4 py-3 font-medium">Detected</th>
                <th className="px-4 py-3 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {holds.map((hold) => {
                const open = openId === hold.id;
                return (
                  <Fragment key={hold.id}>
                    <tr
                      className="border-t"
                      style={{
                        borderColor: 'var(--er-border)',
                        background: open ? 'var(--er-surface-2)' : 'var(--er-surface)',
                      }}
                    >
                      <td className="px-4 py-3">
                        <button
                          type="button"
                          aria-expanded={open}
                          aria-controls={`dossier-${hold.id}`}
                          onClick={() => setOpenId(open ? null : hold.id)}
                          className="p-1"
                          style={{ color: 'var(--er-muted)' }}
                          aria-label={open ? 'Collapse evidence' : 'Inspect evidence'}
                        >
                          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                        </button>
                      </td>
                      <td className="px-4 py-3 font-medium">{hold.campaign}</td>
                      <td className="px-4 py-3" style={{ color: 'var(--er-muted)' }}>
                        {hold.violation}
                      </td>
                      <td className="er-mono px-4 py-3 text-xs">{hold.detected}</td>
                      <td className="px-4 py-3">
                        <span
                          className={`er-status ${
                            hold.status === 'held' ? 'er-status-held' : 'er-status-pass'
                          }`}
                        >
                          <span className="er-status-dot" />
                          {hold.status === 'held' ? 'Held' : 'Resolved'}
                        </span>
                      </td>
                    </tr>
                    {open && (
                      <tr style={{ background: 'var(--er-surface-2)' }}>
                        <td colSpan={5} className="p-0">
                          <div
                            id={`dossier-${hold.id}`}
                            className="border-t"
                            style={{
                              borderColor: 'var(--er-accent)',
                              borderTopWidth: 2,
                            }}
                          >
                            <div className="grid grid-cols-1 gap-6 px-5 py-5 lg:grid-cols-2">
                              <div>
                                <p className="er-label">Intended action</p>
                                <p className="mt-2 text-sm font-medium">{hold.intendedAction}</p>
                                <p className="er-mono mt-2 text-[11px]" style={{ color: 'var(--er-muted)' }}>
                                  {hold.id}
                                </p>
                              </div>
                              <div>
                                <p className="er-label">Violation</p>
                                <p className="mt-2 text-sm font-medium" style={{ color: 'var(--er-critical)' }}>
                                  {hold.violation}
                                </p>
                              </div>
                            </div>

                            <div className="er-seam-open mx-5" />

                            <div className="px-5 py-4">
                              <p className="er-label mb-3">Evidence points</p>
                              <ul
                                className="space-y-1 border p-3"
                                style={{ borderColor: 'var(--er-border)', background: 'var(--er-surface)' }}
                              >
                                {hold.evidencePoints.map((ep) => (
                                  <li
                                    key={ep.key}
                                    className="er-mono flex justify-between gap-4 border-b py-1.5 text-xs last:border-0"
                                    style={{ borderColor: 'var(--er-border)' }}
                                  >
                                    <span style={{ color: 'var(--er-muted)' }}>{ep.key}</span>
                                    <span>{ep.value}</span>
                                  </li>
                                ))}
                              </ul>
                            </div>

                            {hold.status === 'held' ? (
                              <div className="flex flex-wrap gap-2 px-5 pb-5">
                                <button
                                  type="button"
                                  className="er-btn-primary !py-2 text-xs"
                                  onClick={() => resolveHold(hold.id, 'authorize')}
                                >
                                  Authorize override
                                </button>
                                <button
                                  type="button"
                                  className="er-btn-ghost !py-2 text-xs"
                                  onClick={() => resolveHold(hold.id, 'keep')}
                                >
                                  Keep hold
                                </button>
                                <button
                                  type="button"
                                  className="er-btn-ghost !py-2 text-xs"
                                  onClick={() => resolveHold(hold.id, 'adjust')}
                                >
                                  Adjust parameters
                                </button>
                              </div>
                            ) : (
                              <p className="er-mono px-5 pb-5 text-xs" style={{ color: 'var(--er-pass)' }}>
                                Resolved by {hold.resolvedBy} — paper trail written to audit log.
                              </p>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>

        {holds.every((h) => h.status === 'resolved') && (
          <div className="border-t px-5 py-8 text-center" style={{ borderColor: 'var(--er-border)' }}>
            <p className="er-serif text-xl">No decisions require review.</p>
            <p className="mt-2 text-sm" style={{ color: 'var(--er-muted)' }}>
              StratumAI will show recommendations here once connected data meets the configured
              evaluation window.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
