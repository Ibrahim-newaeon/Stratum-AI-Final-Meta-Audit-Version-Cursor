/**
 * Trust-Gated Autopilot — Luminous Control hero module
 * Signal Health → Trust Gate → Automation Decision
 */

import { Lock, Scale, ShieldCheck } from 'lucide-react';

type StageState = 'pass' | 'hold' | 'block' | 'processing' | 'unknown';

interface Stage {
  title: string;
  icon: typeof ShieldCheck;
  state: StageState;
  reason: string;
  evaluatedAt: string;
}

const STAGES: Stage[] = [
  {
    title: 'Signal Health',
    icon: ShieldCheck,
    state: 'pass',
    reason: 'Meta Ads freshness within 8 min · coverage 98.4%',
    evaluatedAt: '09:42 UTC',
  },
  {
    title: 'Trust Gate',
    icon: Lock,
    state: 'hold',
    reason: 'Conversion lag 11h exceeds evidence window',
    evaluatedAt: '09:42 UTC',
  },
  {
    title: 'Automation Decision',
    icon: Scale,
    state: 'block',
    reason: 'Budget increase held — awaiting operator review',
    evaluatedAt: '09:42 UTC',
  },
];

const badgeClass: Record<StageState, string> = {
  pass: 'lc-badge-pass',
  hold: 'lc-badge-hold',
  block: 'lc-badge-block',
  processing: 'lc-badge-unknown',
  unknown: 'lc-badge-unknown',
};

const badgeLabel: Record<StageState, string> = {
  pass: 'Pass',
  hold: 'Hold',
  block: 'Block',
  processing: 'Processing',
  unknown: 'Unknown',
};

export function TrustGatedAutopilotModule() {
  return (
    <section className="lc-card lc-glow-accent flex h-full flex-col p-5" aria-labelledby="tga-title">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="lc-label">Hero module</p>
          <h2 id="tga-title" className="mt-1 text-lg font-semibold tracking-tight">
            Trust-Gated Autopilot
          </h2>
          <p className="mt-1 text-sm" style={{ color: 'var(--lc-muted)' }}>
            Live decision path — signals checked before money moves
          </p>
        </div>
        <span className="lc-badge lc-badge-pass">
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: 'var(--lc-pass)' }} />
          Live flow
        </span>
      </div>

      <div className="relative flex flex-1 flex-col justify-center gap-4 md:flex-row md:items-stretch md:gap-0">
        {STAGES.map((stage, i) => {
          const Icon = stage.icon;
          return (
            <div key={stage.title} className="relative flex flex-1 flex-col md:px-2">
              {i < STAGES.length - 1 && (
                <div
                  className="pointer-events-none absolute left-[calc(50%+40px)] right-[-8px] top-10 hidden h-[2px] md:block"
                  style={{
                    background: `linear-gradient(90deg, var(--lc-accent), color-mix(in srgb, var(--lc-accent) 30%, transparent))`,
                    opacity: 0.55,
                  }}
                  aria-hidden
                />
              )}
              <div
                className="flex flex-1 flex-col rounded-[var(--lc-radius)] border p-4"
                style={{
                  borderColor: 'var(--lc-border)',
                  background: 'var(--lc-canvas)',
                }}
              >
                <div className="mb-3 flex items-center justify-between gap-2">
                  <div
                    className="flex h-10 w-10 items-center justify-center rounded-[10px]"
                    style={{
                      background: 'var(--lc-selected)',
                      color: 'var(--lc-accent)',
                    }}
                  >
                    <Icon size={20} strokeWidth={1.75} />
                  </div>
                  <span className={`lc-badge ${badgeClass[stage.state]}`}>
                    {badgeLabel[stage.state]}
                  </span>
                </div>
                <h3 className="text-sm font-semibold">{stage.title}</h3>
                <p className="mt-2 flex-1 text-xs leading-5" style={{ color: 'var(--lc-muted)' }}>
                  {stage.reason}
                </p>
                <p className="lc-mono mt-3 text-[10px]" style={{ color: 'var(--lc-muted)' }}>
                  Evaluated {stage.evaluatedAt}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
