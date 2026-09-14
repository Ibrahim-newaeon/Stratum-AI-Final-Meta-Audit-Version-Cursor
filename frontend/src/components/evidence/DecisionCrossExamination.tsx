/**
 * Interactive decision dossier — Cross-Examination (marketing).
 * Clearly labeled sample scenario. Deterministic, accessible.
 */

import { useId, useState } from 'react';
import { SAMPLE_LABEL } from '@/theme/evidence';

type Scenario = 'healthy' | 'delayed' | 'anomaly';

const SCENARIOS: { id: Scenario; label: string }[] = [
  { id: 'healthy', label: 'Healthy signals' },
  { id: 'delayed', label: 'Delayed conversions' },
  { id: 'anomaly', label: 'Tracking anomaly' },
];

const DATA: Record<
  Scenario,
  {
    status: 'READY' | 'HELD';
    statusClass: string;
    checks: { label: string; value: string; ok: boolean }[];
    consequence: string;
    next: string;
    whatChanged: string;
    why: string;
  }
> = {
  healthy: {
    status: 'READY',
    statusClass: 'er-status-pass',
    checks: [
      { label: 'Conversion lag', value: '42 minutes', ok: true },
      { label: 'Tracking status', value: 'Consistent', ok: true },
      { label: 'Spend pacing', value: 'Within policy', ok: true },
      { label: 'Signal health', value: 'Clear', ok: true },
    ],
    consequence: 'Recommendation may proceed to approval if permissions allow.',
    next: 'Authorize the increase or keep it held for later review.',
    whatChanged: 'All configured evidence checks passed within the evaluation window.',
    why: 'Fresh conversion data and consistent tracking support a budget change.',
  },
  delayed: {
    status: 'HELD',
    statusClass: 'er-status-held',
    checks: [
      { label: 'Conversion lag', value: '11 hours', ok: false },
      { label: 'Tracking status', value: 'Consistent', ok: true },
      { label: 'Spend pacing', value: 'Within policy', ok: true },
      { label: 'Signal health', value: 'Degraded', ok: false },
    ],
    consequence: 'The proposed budget increase was not executed.',
    next: 'Review the data connection or authorize a manual decision.',
    whatChanged: 'Conversion reporting delayed beyond the configured evidence window.',
    why: 'Acting on incomplete conversions can overspend against stale performance.',
  },
  anomaly: {
    status: 'HELD',
    statusClass: 'er-status-held',
    checks: [
      { label: 'Conversion lag', value: '1.2 hours', ok: true },
      { label: 'Tracking status', value: 'Anomaly detected', ok: false },
      { label: 'Spend pacing', value: 'Within policy', ok: true },
      { label: 'Signal health', value: 'Review', ok: false },
    ],
    consequence: 'Automation refused to execute until tracking integrity is confirmed.',
    next: 'Inspect the affected source, then approve an exception or keep held.',
    whatChanged: 'One source reported inconsistent event volumes versus baseline.',
    why: 'Anomalous tracking can invent or hide conversions used for budget decisions.',
  },
};

export function DecisionCrossExamination() {
  const [scenario, setScenario] = useState<Scenario>('delayed');
  const [openSection, setOpenSection] = useState<string | null>('what');
  const labelId = useId();
  const data = DATA[scenario];
  const seamOpen = true;

  return (
    <section
      className="er-panel"
      aria-labelledby={labelId}
      data-sample="true"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4" style={{ borderColor: 'var(--er-border)' }}>
        <div>
          <p id={labelId} className="er-label">
            Cross-examination
          </p>
          <h2 className="er-serif mt-1 text-2xl md:text-3xl" style={{ color: 'var(--er-text)' }}>
            Decision dossier
          </h2>
        </div>
        <p className="er-mono text-[11px]" style={{ color: 'var(--er-muted)' }}>
          {SAMPLE_LABEL}
        </p>
      </div>

      <div className="flex flex-wrap gap-2 border-b px-5 py-3" style={{ borderColor: 'var(--er-border)' }} role="tablist" aria-label="Sample scenarios">
        {SCENARIOS.map((s) => {
          const selected = scenario === s.id;
          return (
            <button
              key={s.id}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => setScenario(s.id)}
              className="px-3 py-2 text-sm"
              style={{
                background: selected ? 'var(--er-surface-2)' : 'transparent',
                color: selected ? 'var(--er-text)' : 'var(--er-muted)',
                borderBottom: selected ? '2px solid var(--er-accent)' : '2px solid transparent',
                borderRadius: 'var(--er-radius)',
              }}
            >
              {s.label}
            </button>
          );
        })}
      </div>

      {/* Recommendation */}
      <div className="px-5 py-5">
        <p className="er-label">Proposed action</p>
        <h3 className="mt-2 text-lg font-semibold tracking-tight">INCREASE DAILY BUDGET</h3>
        <dl className="mt-3 grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
          <div>
            <dt className="er-muted" style={{ color: 'var(--er-muted)' }}>
              Campaign
            </dt>
            <dd>Spring Acquisition / Search</dd>
          </div>
          <div>
            <dt style={{ color: 'var(--er-muted)' }}>Proposed change</dt>
            <dd className="er-mono">+18%</dd>
          </div>
          <div>
            <dt style={{ color: 'var(--er-muted)' }}>Decision ID</dt>
            <dd className="er-mono">DEC-2026-0914-0042</dd>
          </div>
          <div>
            <dt style={{ color: 'var(--er-muted)' }}>Status</dt>
            <dd className={`er-status ${data.statusClass}`}>
              <span className="er-status-dot" aria-hidden />
              {data.status === 'HELD' ? 'Held for review' : 'Ready for approval'}
            </dd>
          </div>
        </dl>
      </div>

      {/* Seam */}
      <div
        className={seamOpen ? 'er-seam-open' : 'er-seam'}
        role="separator"
        aria-label="Evidence seam"
      />

      {/* Evidence */}
      <div
        className="er-tray mx-5 my-5 p-4"
        aria-live="polite"
      >
        <p className="er-label mb-3">Evidence checks</p>
        <ul className="space-y-2">
          {data.checks.map((c) => (
            <li
              key={c.label}
              className="flex items-center justify-between gap-4 border-b py-2 text-sm last:border-0"
              style={{ borderColor: 'var(--er-border)' }}
            >
              <span style={{ color: 'var(--er-muted)' }}>{c.label}</span>
              <span className={`er-mono ${c.ok ? 'er-status-pass' : 'er-status-held'}`}>
                {c.value}
              </span>
            </li>
          ))}
        </ul>
      </div>

      {data.status === 'HELD' && (
        <div
          className="mx-5 mb-5 border p-4"
          style={{ borderColor: 'var(--er-critical)', background: 'var(--er-surface)' }}
          role="status"
        >
          <p className="er-status er-status-held mb-2">
            <span className="er-status-dot" /> Held
          </p>
          <p className="text-sm font-medium">{data.consequence}</p>
          <p className="mt-2 text-sm" style={{ color: 'var(--er-muted)' }}>
            <strong style={{ color: 'var(--er-text)' }}>Next step:</strong> {data.next}
          </p>
        </div>
      )}

      {/* Expandable sections */}
      <div className="border-t px-5 py-2" style={{ borderColor: 'var(--er-border)' }}>
        {(
          [
            ['what', 'What changed', data.whatChanged],
            ['why', 'Why this matters', data.why],
            ['next', 'What happens next', data.next],
          ] as const
        ).map(([id, title, body]) => {
          const open = openSection === id;
          return (
            <div key={id} className="border-b" style={{ borderColor: 'var(--er-border)' }}>
              <button
                type="button"
                className="flex w-full items-center justify-between py-3 text-left text-sm font-medium"
                aria-expanded={open}
                onClick={() => setOpenSection(open ? null : id)}
              >
                {title}
                <span className="er-mono text-xs" style={{ color: 'var(--er-muted)' }}>
                  {open ? '−' : '+'}
                </span>
              </button>
              {open && (
                <p className="pb-3 text-sm leading-6" style={{ color: 'var(--er-muted)' }}>
                  {body}
                </p>
              )}
            </div>
          );
        })}
      </div>

      <p className="px-5 py-4 text-xs leading-5" style={{ color: 'var(--er-muted)' }}>
        This interactive dossier uses a deterministic sample scenario. It is not connected to your
        account and does not reflect live advertising data.
      </p>
    </section>
  );
}
