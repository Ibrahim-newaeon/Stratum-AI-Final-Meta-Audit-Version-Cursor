import { AlertTriangle, ArrowUpRight, CheckCircle2, PauseCircle, ShieldAlert } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Kinetic Signal Observatory — dashboard overview (design sample).
 * Asymmetric modules: Signal Weather, Trust Gate Decision, Campaign Pulse, Evidence Stream, Forecast.
 */
export function KineticDashboardOverview() {
  return (
    <div className="mx-auto max-w-[1280px] space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="ks-label">14 Sep 2026 · last sync 09:42 UTC</p>
          <h1 className="ks-display mt-1 text-3xl sm:text-4xl" style={{ color: 'var(--ks-ink)' }}>
            Signal Weather
          </h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--ks-muted)' }}>
            Live operational instrument — sample data labeled
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="ks-badge ks-badge-pass">Meta connected</span>
          <span className="ks-badge ks-badge-hold">Hard-Block mode</span>
          <span
            className="ks-mono border border-[var(--ks-line)] px-2 py-1 text-[10px]"
            style={{ color: 'var(--ks-muted)' }}
          >
            Sample · not live metrics
          </span>
        </div>
      </div>

      {/* Irregular top composition */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <section
          className="ks-rise relative overflow-hidden lg:col-span-7"
          style={{ background: 'var(--ks-surface-blue)', border: '1px solid var(--ks-line)' }}
        >
          <div className="ks-ribbon" />
          <div className="p-5 sm:p-6">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="ks-label">Overall condition</p>
                <p className="ks-display mt-1 text-5xl sm:text-6xl" style={{ color: 'var(--ks-pass)' }}>
                  72
                </p>
                <p className="ks-mono mt-1 text-sm" style={{ color: 'var(--ks-pass)' }}>
                  HEALTHY · PASS
                </p>
              </div>
              <button type="button" className="ks-btn-secondary !py-2 !text-xs">
                Details
              </button>
            </div>
            <div className="mt-6 grid gap-3 sm:grid-cols-5">
              {COMPONENTS.map((c) => (
                <div key={c.name} className="border border-[var(--ks-line)] bg-[var(--ks-surface)] p-2.5">
                  <p className="ks-label truncate">{c.name}</p>
                  <p
                    className="ks-mono mt-1 text-lg font-semibold"
                    style={{ color: c.ok ? 'var(--ks-pass)' : 'var(--ks-hold)' }}
                  >
                    {c.score}
                  </p>
                </div>
              ))}
            </div>
            <p className="ks-mono mt-4 text-[10px]" style={{ color: 'var(--ks-muted)' }}>
              Evaluated 09:42 UTC · trend stable 24h
            </p>
          </div>
        </section>

        <section
          className="ks-rise relative lg:col-span-5"
          style={{
            background: 'var(--ks-surface)',
            border: '1px solid var(--ks-line)',
            boxShadow: 'var(--ks-glow)',
            animationDelay: '0.08s',
          }}
        >
          <div className="p-5 sm:p-6">
            <p className="ks-label">Trust Gate Decision</p>
            <p className="ks-display mt-2 text-2xl" style={{ color: 'var(--ks-ink)' }}>
              Budget +12% · Catalog A
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <span className="ks-badge ks-badge-hold">HOLD</span>
              <span className="ks-mono text-[11px]" style={{ color: 'var(--ks-muted)' }}>
                Confidence 0.61 · 4 evidence points
              </span>
            </div>
            <p className="mt-4 text-sm leading-relaxed" style={{ color: 'var(--ks-muted)' }}>
              Conversion lag exceeds the evidence window. Autopilot alerts only — no Meta write
              until an operator reviews or signal recovers.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <button type="button" className="ks-btn-primary !text-xs">
                Human override
              </button>
              <button type="button" className="ks-btn-secondary !text-xs">
                Audit history
              </button>
            </div>
            {/* Segmented path */}
            <div className="mt-6 flex items-center gap-1">
              {['Signal', 'Gate', 'Decision'].map((step, i) => (
                <div key={step} className="flex flex-1 items-center gap-1">
                  <div
                    className="flex-1 border px-2 py-2 text-center ks-mono text-[10px]"
                    style={{
                      borderColor: 'var(--ks-line)',
                      background: i === 1 ? 'var(--ks-surface-coral)' : 'var(--ks-surface-blue)',
                      color: i === 1 ? 'var(--ks-hold)' : 'var(--ks-ink)',
                    }}
                  >
                    {step}
                  </div>
                  {i < 2 ? (
                    <ArrowUpRight className="h-3 w-3 shrink-0" style={{ color: 'var(--ks-cyan)' }} />
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>

      {/* Campaign pulse + evidence */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
        <section
          className="xl:col-span-7"
          style={{ background: 'var(--ks-surface)', border: '1px solid var(--ks-line)' }}
        >
          <div className="flex items-center justify-between border-b border-[var(--ks-line)] px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold">Campaign Pulse</h2>
              <p className="ks-label mt-0.5">Flowing rows · not equal cards</p>
            </div>
          </div>
          <ul className="divide-y divide-[var(--ks-line)]">
            {CAMPAIGNS.map((c) => (
              <li key={c.name} className="flex flex-wrap items-stretch gap-0">
                <div className="w-1.5 self-stretch" style={{ background: c.strip }} aria-hidden />
                <div className="flex min-w-0 flex-1 flex-wrap items-center gap-3 px-4 py-3 text-sm">
                  <div className="min-w-[140px] flex-1">
                    <p className="font-medium">{c.name}</p>
                    <p className="ks-mono text-[10px]" style={{ color: 'var(--ks-muted)' }}>
                      {c.platform}
                    </p>
                  </div>
                  <Metric label="Spend" value={c.spend} />
                  <Metric label="ROAS" value={c.roas} warn={c.roas === '—'} />
                  <Metric label="Health" value={c.health} />
                  <span className={cn('ks-badge', c.decisionClass)}>{c.decision}</span>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section
          className="xl:col-span-5"
          style={{ background: 'var(--ks-surface)', border: '1px solid var(--ks-line)' }}
        >
          <div className="border-b border-[var(--ks-line)] px-4 py-3">
            <h2 className="text-sm font-semibold">Evidence Stream</h2>
            <p className="ks-label mt-0.5">Reasons + timestamps</p>
          </div>
          <ul className="max-h-[320px] space-y-0 overflow-y-auto">
            {EVIDENCE.map((e) => (
              <li
                key={e.id}
                className="flex gap-3 border-b border-[var(--ks-line)] px-4 py-3 last:border-0"
              >
                <e.Icon
                  className="mt-0.5 h-4 w-4 shrink-0"
                  style={{ color: e.color }}
                  aria-hidden
                />
                <div>
                  <p className="text-sm font-medium">{e.title}</p>
                  <p className="mt-0.5 text-xs" style={{ color: 'var(--ks-muted)' }}>
                    {e.reason}
                  </p>
                  <p className="ks-mono mt-1 text-[10px]" style={{ color: 'var(--ks-muted)' }}>
                    {e.at}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </section>
      </div>

      {/* Forecast horizon + quick actions */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <section
          className="relative overflow-hidden lg:col-span-8"
          style={{ background: 'var(--ks-surface-lime)', border: '1px solid var(--ks-line)' }}
        >
          <div className="p-5">
            <p className="ks-label">Forecast Horizon</p>
            <h2 className="ks-display mt-1 text-2xl">Pacing path · next 7d</h2>
            <svg viewBox="0 0 480 120" className="mt-4 h-28 w-full" aria-label="Forecast wave sample">
              <path
                d="M0 80 C 60 70, 100 40, 160 50 S 260 90, 320 55 S 400 20, 480 35"
                fill="none"
                stroke="var(--ks-cobalt)"
                strokeWidth="2.5"
              />
              <path
                d="M0 90 C 60 85, 100 60, 160 65 S 260 100, 320 70 S 400 40, 480 50"
                fill="none"
                stroke="var(--ks-cyan)"
                strokeWidth="1.5"
                strokeDasharray="4 4"
                opacity="0.7"
              />
            </svg>
            <div className="mt-2 flex flex-wrap gap-4 text-sm">
              <span>
                Predicted spend <strong className="ks-mono">$28.4k</strong>
              </span>
              <span>
                Expected ROAS <strong className="ks-mono">—</strong>
                <span className="ks-label ml-1">undefined until attribution sync</span>
              </span>
              <span>
                Risk <strong style={{ color: 'var(--ks-hold)' }}>elevated pacing</strong>
              </span>
            </div>
          </div>
        </section>

        <section
          className="lg:col-span-4"
          style={{ background: 'var(--ks-surface)', border: '1px solid var(--ks-line)' }}
        >
          <div className="border-b border-[var(--ks-line)] px-4 py-3">
            <h2 className="text-sm font-semibold">Quick actions</h2>
          </div>
          <ul className="p-2">
            {[
              'Review blocked decisions',
              'Inspect signal health',
              'Connect Meta account',
              'Review measurement variance',
              'Create campaign',
            ].map((a) => (
              <li key={a}>
                <button
                  type="button"
                  className="flex w-full items-center justify-between px-3 py-2.5 text-left text-sm hover:bg-[var(--ks-cobalt-soft)]"
                >
                  {a}
                  <ArrowUpRight className="h-3.5 w-3.5" style={{ color: 'var(--ks-cobalt)' }} />
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  )
}

function Metric({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="min-w-[64px]">
      <p className="ks-label">{label}</p>
      <p
        className="ks-mono text-sm font-semibold"
        style={{ color: warn ? 'var(--ks-hold)' : 'var(--ks-ink)' }}
      >
        {value}
      </p>
    </div>
  )
}

const COMPONENTS = [
  { name: 'Freshness', score: '94', ok: true },
  { name: 'Integrity', score: '81', ok: true },
  { name: 'vs GA4', score: '58', ok: false },
  { name: 'EMQ', score: '88', ok: true },
  { name: 'Pacing', score: '76', ok: true },
]

const CAMPAIGNS = [
  {
    name: 'Prospecting · Catalog',
    platform: 'Meta · Facebook',
    spend: '$1,240',
    roas: '—',
    health: '72',
    decision: 'HOLD',
    decisionClass: 'ks-badge-hold',
    strip: 'var(--ks-hold)',
  },
  {
    name: 'Retargeting · 7d',
    platform: 'Meta · Instagram',
    spend: '$860',
    roas: '—',
    health: '84',
    decision: 'EXECUTE',
    decisionClass: 'ks-badge-pass',
    strip: 'var(--ks-pass)',
  },
  {
    name: 'WhatsApp · Lead',
    platform: 'Meta · WhatsApp',
    spend: '$410',
    roas: '—',
    health: '41',
    decision: 'BLOCK',
    decisionClass: 'ks-badge-block',
    strip: 'var(--ks-block)',
  },
]

const EVIDENCE = [
  {
    id: '1',
    Icon: PauseCircle,
    color: 'var(--ks-hold)',
    title: 'Hold · budget +12%',
    reason: 'Conversion lag 11h · gate refused automatic write',
    at: '09:42 UTC',
  },
  {
    id: '2',
    Icon: CheckCircle2,
    color: 'var(--ks-pass)',
    title: 'Pass · pause creative Hook v2',
    reason: 'Signal healthy · operator approved · applied',
    at: '09:18 UTC',
  },
  {
    id: '3',
    Icon: ShieldAlert,
    color: 'var(--ks-block)',
    title: 'Block · bid floor change',
    reason: 'Platform vs GA4 variance unhealthy',
    at: '08:55 UTC',
  },
  {
    id: '4',
    Icon: AlertTriangle,
    color: 'var(--ks-orange)',
    title: 'Data quality alert',
    reason: 'Event freshness degraded on secondary pixel',
    at: '08:40 UTC',
  },
]

export function KineticPlaceholder({ title }: { title: string }) {
  return (
    <div className="mx-auto max-w-lg border border-[var(--ks-line)] bg-[var(--ks-surface)] p-8 text-center">
      <p className="ks-label">Design preview</p>
      <h1 className="ks-display mt-2 text-3xl">{title}</h1>
      <p className="mt-2 text-sm" style={{ color: 'var(--ks-muted)' }}>
        Overview is the approval surface. This module ships after Kinetic sign-off.
      </p>
    </div>
  )
}

export default KineticDashboardOverview
