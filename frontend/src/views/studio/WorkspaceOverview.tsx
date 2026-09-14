/**
 * Dashboard Overview — Attention Ledger + Decision Under Review
 * Sample data clearly labeled. Empty/error states intentional.
 */

import { Link } from 'react-router-dom';
import { SAMPLE_LABEL } from '@/theme/evidence';
import { useActivationStatus } from '@/api/activation';

const ATTENTION = [
  { label: 'Trust holds', count: 1, href: '#trust-holds' },
  { label: 'Signal drops', count: 3, href: '#signal-health' },
  { label: 'Pending reviews', count: 2, href: '#decision' },
  { label: 'Recommendations ready', count: 5, href: '/dashboard/recommendations' },
  { label: 'Actions executed today', count: 8, href: '#trail' },
];

const METRICS = [
  {
    title: 'Trust Gate',
    value: 'CLEAR',
    detail: 'All configured checks passed',
    status: 'pass' as const,
  },
  {
    title: 'Signal Health',
    value: 'DEGRADED',
    detail: '2 sources require review',
    status: 'warn' as const,
  },
  {
    title: 'ROAS Today',
    value: 'NOT AVAILABLE',
    detail: 'Connect conversion revenue data to calculate this metric.',
    status: 'muted' as const,
  },
  {
    title: 'Pacing Today',
    value: 'WITHIN POLICY',
    detail: 'Spend pacing inside configured band',
    status: 'pass' as const,
  },
];

const SIGNALS = [
  { source: 'Meta Ads', freshness: '8 min ago', coverage: '98.4%', state: 'Healthy', impact: 'None' },
  { source: 'Web conversion events', freshness: '11 hr ago', coverage: '74.8%', state: 'Delayed', impact: '5 decisions' },
  { source: 'Audience sync', freshness: '—', coverage: '—', state: 'Unavailable', impact: 'Custom Audiences' },
];

const TRAIL = [
  {
    id: 'DEC-2026-0914-0042',
    action: 'Increase daily budget by 18%',
    result: 'HELD',
    reason: 'Conversion data delayed',
    at: '14 Sep 2026, 09:31 UTC',
  },
  {
    id: 'DEC-2026-0914-0038',
    action: 'Pause underperforming ad set',
    result: 'EXECUTED',
    reason: 'Policy + trust gate clear',
    at: '14 Sep 2026, 08:12 UTC',
  },
  {
    id: 'DEC-2026-0913-0110',
    action: 'Increase bid cap 5%',
    result: 'APPROVED',
    reason: 'Manual authorization',
    at: '13 Sep 2026, 16:44 UTC',
  },
];

const HOLDS = [
  {
    campaign: 'Spring Acquisition / Search',
    action: 'Increase daily budget +18%',
    reason: 'Conversion lag beyond evidence window',
    detected: '14 Sep 2026, 09:31 UTC',
    evidence: 'Degraded',
    owner: 'Unassigned',
  },
];

export default function EvidenceOverview() {
  const { data: activation } = useActivationStatus();
  const setupDone = activation?.required_done ?? 0;
  const setupTotal = activation?.required_total ?? 3;

  return (
    <div className="mx-auto max-w-[1200px] space-y-8 px-6 py-8">
      {/* Getting started */}
      <div className="er-panel p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="er-label">Getting started</p>
            <p className="mt-1 text-sm" style={{ color: 'var(--er-muted)' }}>
              {setupDone} / {setupTotal} Meta integration steps complete — connect OAuth, CAPI, and
              Marketing API token.
            </p>
          </div>
          <Link to="/dashboard/activation" className="er-btn-primary !py-2 !px-3 text-sm">
            Resume setup
          </Link>
        </div>
        <div className="mt-4 h-1 w-full" style={{ background: 'var(--er-border)' }}>
          <div
            className="h-1"
            style={{
              width: `${activation?.progress_percent ?? 0}%`,
              background: 'var(--er-accent)',
            }}
          />
        </div>
      </div>

      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="er-serif text-4xl">Overview</h1>
          <p className="mt-2 text-sm" style={{ color: 'var(--er-muted)' }}>
            The decisions, holds, and signal conditions requiring attention.
          </p>
        </div>
        <div className="text-right">
          <p className="er-mono text-[11px]" style={{ color: 'var(--er-muted)' }}>
            Data refreshed 14 Sep 2026 at 09:42 UTC
          </p>
          <p className="er-mono mt-1 text-[11px]" style={{ color: 'var(--er-muted)' }}>
            {SAMPLE_LABEL}
          </p>
        </div>
      </div>

      {/* Attention Ledger */}
      <section className="er-panel" aria-labelledby="attention-heading">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4" style={{ borderColor: 'var(--er-border)' }}>
          <h2 id="attention-heading" className="er-label" style={{ color: 'var(--er-text)' }}>
            Attention ledger
          </h2>
          <div className="flex gap-2">
            <a href="#decision" className="er-btn-primary !py-2 !px-3 text-sm">
              Review attention items
            </a>
            <button type="button" className="er-btn-ghost !py-2 !px-3 text-sm">
              Acknowledge all
            </button>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-px md:grid-cols-5" style={{ background: 'var(--er-border)' }}>
          {ATTENTION.map((a) => (
            <Link
              key={a.label}
              to={a.href}
              className="block p-4 no-underline"
              style={{ background: 'var(--er-surface)', color: 'inherit' }}
            >
              <div className="er-mono text-2xl font-medium">{a.count}</div>
              <div className="mt-1 text-xs" style={{ color: 'var(--er-muted)' }}>
                {a.label}
              </div>
            </Link>
          ))}
        </div>
      </section>

      {/* Decision under review */}
      <section id="decision" className="er-panel scroll-mt-8" aria-labelledby="decision-heading">
        <div className="border-b px-5 py-4" style={{ borderColor: 'var(--er-border)' }}>
          <p className="er-label">Decision under review</p>
          <h2 id="decision-heading" className="er-serif mt-1 text-3xl">
            Increase daily budget
          </h2>
          <p className="mt-2 text-sm" style={{ color: 'var(--er-muted)' }}>
            Campaign: Spring Acquisition / Search · Proposed change: +18% · Decision ID:{' '}
            <span className="er-mono">DEC-2026-0914-0042</span>
          </p>
          <p className="er-status er-status-held mt-3">
            <span className="er-status-dot" /> Held for review
          </p>
        </div>

        <div className="er-seam-open" />

        <div className="grid grid-cols-1 gap-px border-b md:grid-cols-5" style={{ background: 'var(--er-border)', borderColor: 'var(--er-border)' }}>
          {[
            ['Signal health', 'Degraded'],
            ['Conversion lag', '11 hours'],
            ['Tracking status', 'Anomaly detected'],
            ['Pacing', 'Within policy'],
            ['Permission scope', 'Approval required'],
          ].map(([k, v]) => (
            <div key={k} className="p-4" style={{ background: 'var(--er-surface)' }}>
              <div className="er-label">{k}</div>
              <div className="mt-2 text-sm font-medium">{v}</div>
            </div>
          ))}
        </div>

        <div className="er-tray m-5 p-4">
          <p className="text-sm font-medium">Why this has not happened</p>
          <p className="mt-2 text-sm leading-6" style={{ color: 'var(--er-muted)' }}>
            Conversion data is delayed beyond the configured evidence window. The proposed budget
            increase was not executed. Review the data connection or authorize a manual decision.
          </p>
        </div>

        <div className="flex flex-wrap gap-2 px-5 pb-5">
          <button type="button" className="er-btn-primary">
            Inspect evidence
          </button>
          <button type="button" className="er-btn-ghost">
            Approve
          </button>
          <button type="button" className="er-btn-ghost">
            Hold
          </button>
          <button type="button" className="er-btn-ghost">
            Dismiss
          </button>
          <Link to="/dashboard/recommendations" className="er-btn-ghost">
            Open audit trail
          </Link>
        </div>
      </section>

      {/* Core metrics */}
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {METRICS.map((m) => (
          <div key={m.title} className="er-panel p-4">
            <p className="er-label">{m.title}</p>
            <p
              className={`er-mono mt-3 text-sm font-medium ${
                m.status === 'pass'
                  ? 'er-status-pass'
                  : m.status === 'warn'
                    ? 'er-status-warn'
                    : ''
              }`}
              style={m.status === 'muted' ? { color: 'var(--er-muted)' } : undefined}
            >
              {m.value}
            </p>
            <p className="mt-2 text-xs leading-5" style={{ color: 'var(--er-muted)' }}>
              {m.detail}
            </p>
            <p className="er-mono mt-3 text-[10px]" style={{ color: 'var(--er-muted)' }}>
              Updated 09:42 UTC
            </p>
          </div>
        ))}
      </section>

      {/* Signal health */}
      <section id="signal-health" className="er-panel scroll-mt-8">
        <div className="border-b px-5 py-4" style={{ borderColor: 'var(--er-border)' }}>
          <h2 className="er-label" style={{ color: 'var(--er-text)' }}>
            Signal health
          </h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr style={{ color: 'var(--er-muted)' }}>
                <th className="px-5 py-3 font-medium">Source</th>
                <th className="px-5 py-3 font-medium">Freshness</th>
                <th className="px-5 py-3 font-medium">Coverage</th>
                <th className="px-5 py-3 font-medium">State</th>
                <th className="px-5 py-3 font-medium">Impact</th>
              </tr>
            </thead>
            <tbody>
              {SIGNALS.map((r) => (
                <tr key={r.source} className="border-t" style={{ borderColor: 'var(--er-border)' }}>
                  <td className="px-5 py-3 font-medium">{r.source}</td>
                  <td className="er-mono px-5 py-3 text-xs">{r.freshness}</td>
                  <td className="er-mono px-5 py-3 text-xs">{r.coverage}</td>
                  <td className="px-5 py-3">
                    <span
                      className={`er-status ${
                        r.state === 'Healthy'
                          ? 'er-status-pass'
                          : r.state === 'Delayed'
                            ? 'er-status-warn'
                            : 'er-status-held'
                      }`}
                    >
                      <span className="er-status-dot" />
                      {r.state}
                    </span>
                  </td>
                  <td className="px-5 py-3" style={{ color: 'var(--er-muted)' }}>
                    {r.impact}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Decision trail + Trust holds */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section id="trail" className="er-panel">
          <div className="border-b px-5 py-4" style={{ borderColor: 'var(--er-border)' }}>
            <h2 className="er-label" style={{ color: 'var(--er-text)' }}>
              Decision trail
            </h2>
          </div>
          <ul>
            {TRAIL.map((t) => (
              <li
                key={t.id}
                className="border-b px-5 py-4 last:border-0"
                style={{ borderColor: 'var(--er-border)' }}
              >
                <div className="er-mono text-[11px]" style={{ color: 'var(--er-muted)' }}>
                  {t.id}
                </div>
                <div className="mt-1 text-sm font-medium">{t.action}</div>
                <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
                  <span
                    className={`er-status ${
                      t.result === 'HELD'
                        ? 'er-status-held'
                        : t.result === 'EXECUTED' || t.result === 'APPROVED'
                          ? 'er-status-pass'
                          : ''
                    }`}
                  >
                    <span className="er-status-dot" />
                    {t.result}
                  </span>
                  <span style={{ color: 'var(--er-muted)' }}>{t.reason}</span>
                  <span className="er-mono" style={{ color: 'var(--er-muted)' }}>
                    {t.at}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section id="trust-holds" className="er-panel scroll-mt-8">
          <div className="border-b px-5 py-4" style={{ borderColor: 'var(--er-border)' }}>
            <h2 className="er-label" style={{ color: 'var(--er-text)' }}>
              Trust holds
            </h2>
            <p className="mt-1 text-xs" style={{ color: 'var(--er-muted)' }}>
              Automations currently held because the available evidence does not meet policy.
            </p>
          </div>
          {HOLDS.map((h) => (
            <div key={h.campaign} className="space-y-3 px-5 py-4">
              <div className="text-sm font-medium">{h.campaign}</div>
              <div className="text-sm" style={{ color: 'var(--er-muted)' }}>
                {h.action}
              </div>
              <div className="er-tray p-3 text-xs leading-5">
                <div>
                  <span className="er-label">Hold reason</span>
                  <p className="mt-1">{h.reason}</p>
                </div>
                <div className="mt-3 flex flex-wrap gap-4" style={{ color: 'var(--er-muted)' }}>
                  <span>Detected: {h.detected}</span>
                  <span>Evidence: {h.evidence}</span>
                  <span>Owner: {h.owner}</span>
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="er-btn-ghost !py-2 !px-3 text-xs">
                  Inspect evidence
                </button>
                <button type="button" className="er-btn-ghost !py-2 !px-3 text-xs">
                  Assign reviewer
                </button>
                <button type="button" className="er-btn-primary !py-2 !px-3 text-xs">
                  Approve exception
                </button>
              </div>
            </div>
          ))}
        </section>
      </div>
    </div>
  );
}
