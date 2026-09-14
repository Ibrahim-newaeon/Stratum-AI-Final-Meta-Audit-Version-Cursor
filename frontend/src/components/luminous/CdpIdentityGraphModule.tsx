/**
 * CDP Health + Identity Graph — Luminous Control
 * Light Gen Z mix: fluid identity strata chips (not a separate product)
 */

import { useState } from 'react';
import { User } from 'lucide-react';

const NODES = [
  { id: 'core', x: 50, y: 48, active: true, label: 'Resolved' },
  { id: 'email', x: 22, y: 28, active: false, label: 'Email' },
  { id: 'meta', x: 78, y: 26, active: true, label: 'Meta' },
  { id: 'wa', x: 18, y: 68, active: false, label: 'WhatsApp' },
  { id: 'web', x: 82, y: 70, active: false, label: 'Web' },
  { id: 'pixel', x: 50, y: 18, active: false, label: 'Pixel' },
  { id: 'crm', x: 50, y: 82, active: false, label: 'CRM' },
];

const STRATA = ['Intimate', 'Creator', 'Professional', 'Anonymous'] as const;

export function CdpIdentityGraphModule() {
  const [stratum, setStratum] = useState<(typeof STRATA)[number]>('Professional');
  const emq = 87;

  return (
    <section className="lc-card flex h-full flex-col p-5" aria-labelledby="cdp-title">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="lc-label">CDP Pulse</p>
          <h2 id="cdp-title" className="mt-1 text-lg font-semibold">
            Identity Graph
          </h2>
          <p className="mt-1 text-xs" style={{ color: 'var(--lc-muted)' }}>
            How identity resolution feeds Trust Engine
          </p>
        </div>
        <button type="button" className="lc-btn-secondary !px-3 !py-1.5 text-xs">
          Interactive graph
        </button>
      </div>

      {/* Fluid identity strata — Gen Z mix, scoped to CDP context */}
      <div className="mb-4">
        <p className="lc-label mb-2">Identity strata</p>
        <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Identity strata">
          {STRATA.map((s) => {
            const active = stratum === s;
            return (
              <button
                key={s}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setStratum(s)}
                className="rounded-full px-3 py-1 text-xs font-medium"
                style={{
                  background: active ? 'var(--lc-selected)' : 'transparent',
                  color: active ? 'var(--lc-accent)' : 'var(--lc-muted)',
                  border: `1px solid ${active ? 'var(--lc-accent)' : 'var(--lc-border)'}`,
                }}
              >
                {s}
              </button>
            );
          })}
        </div>
      </div>

      <div className="relative min-h-[180px] flex-1 overflow-hidden rounded-[var(--lc-radius)] border" style={{ borderColor: 'var(--lc-border)', background: 'var(--lc-canvas)' }}>
        <svg viewBox="0 0 100 100" className="absolute inset-0 h-full w-full" aria-hidden>
          {NODES.filter((n) => n.id !== 'core').map((n) => (
            <line
              key={`l-${n.id}`}
              x1={50}
              y1={48}
              x2={n.x}
              y2={n.y}
              stroke="var(--lc-border)"
              strokeWidth="0.4"
              strokeDasharray={n.active ? '0' : '1.5 1'}
            />
          ))}
        </svg>
        {NODES.map((n) => (
          <div
            key={n.id}
            className="absolute flex -translate-x-1/2 -translate-y-1/2 flex-col items-center"
            style={{ left: `${n.x}%`, top: `${n.y}%` }}
          >
            <div
              className="flex items-center justify-center rounded-full border"
              style={{
                width: n.id === 'core' ? 36 : 28,
                height: n.id === 'core' ? 36 : 28,
                background: n.active ? 'var(--lc-selected)' : 'var(--lc-surface)',
                borderColor: n.active ? 'var(--lc-accent)' : 'var(--lc-border)',
                color: n.active ? 'var(--lc-accent)' : 'var(--lc-muted)',
                boxShadow: n.active
                  ? '0 0 16px color-mix(in srgb, var(--lc-accent) 25%, transparent)'
                  : undefined,
              }}
            >
              <User size={n.id === 'core' ? 16 : 12} />
            </div>
          </div>
        ))}

        {/* EMQ gauge */}
        <div className="absolute bottom-3 right-3 flex items-center gap-3 rounded-[var(--lc-radius)] border px-3 py-2" style={{ background: 'var(--lc-surface)', borderColor: 'var(--lc-border)' }}>
          <div className="relative h-12 w-12">
            <svg viewBox="0 0 36 36" className="h-12 w-12 -rotate-90">
              <circle cx="18" cy="18" r="15" fill="none" stroke="var(--lc-border)" strokeWidth="3" />
              <circle
                cx="18"
                cy="18"
                r="15"
                fill="none"
                stroke="var(--lc-accent)"
                strokeWidth="3"
                strokeDasharray={`${(emq / 100) * 94} 94`}
                strokeLinecap="round"
              />
            </svg>
            <span className="lc-mono absolute inset-0 flex items-center justify-center text-[10px] font-semibold">
              {emq}
            </span>
          </div>
          <div>
            <p className="lc-label">EMQ</p>
            <p className="text-xs font-medium">Event match quality</p>
          </div>
        </div>
      </div>

      <ul className="mt-4 space-y-1.5 text-xs" style={{ color: 'var(--lc-muted)' }}>
        <li>
          <span className="font-medium" style={{ color: 'var(--lc-text)' }}>
            {stratum}
          </span>{' '}
          context · 1 primary + 6 linked identifiers
        </li>
        <li>Feeds Trust Gate when match coverage drops below policy</li>
      </ul>
    </section>
  );
}
