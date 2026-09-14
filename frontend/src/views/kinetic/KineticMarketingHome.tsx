import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { TrustGateOrbit } from '@/components/kinetic/TrustGateOrbit'

/**
 * Kinetic Signal Observatory — marketing home (design preview).
 * Asymmetric hero; Trust Gate visualization; feature bands (not equal cards).
 */
export function KineticMarketingHome() {
  return (
    <div>
      {/* Hero */}
      <section className="relative overflow-hidden">
        <div
          className="pointer-events-none absolute -right-24 -top-24 h-80 w-80 rounded-full opacity-40 blur-3xl"
          style={{ background: 'var(--ks-cyan)' }}
          aria-hidden
        />
        <div
          className="pointer-events-none absolute -left-16 top-40 h-64 w-64 rotate-12 opacity-30 blur-2xl"
          style={{ background: 'var(--ks-lime)' }}
          aria-hidden
        />

        <div className="mx-auto grid max-w-[1200px] gap-10 px-4 py-12 sm:px-6 lg:grid-cols-12 lg:gap-8 lg:py-16">
          <div className="ks-rise lg:col-span-5 lg:pt-6">
            <p className="ks-label mb-3 inline-flex items-center gap-2">
              <span className="ks-signal-dot" />
              Trust-Gated Autopilot
            </p>
            <h1
              className="ks-display text-[clamp(2.6rem,6vw,4.4rem)]"
              style={{ color: 'var(--ks-ink)' }}
            >
              Automation
              <br />
              should know
              <br />
              <span style={{ color: 'var(--ks-cobalt)' }}>when to stop.</span>
            </h1>
            <p className="mt-5 max-w-md text-[15px] leading-relaxed" style={{ color: 'var(--ks-muted)' }}>
              StratumAI checks the health of your signals before your budget moves. Every
              recommendation is explainable, reversible, and held when the evidence is weak.
            </p>
            <div className="mt-7 flex flex-wrap gap-3">
              <a href="#trust-gate" className="ks-btn-primary">
                Inspect a decision
              </a>
              <Link to="/signup" className="ks-btn-secondary">
                Start free trial
              </Link>
            </div>
            <p className="ks-mono mt-6 text-[11px]" style={{ color: 'var(--ks-muted)' }}>
              Meta acts · GA4 verifies · GTM tags · humans override
            </p>
          </div>

          <div className="ks-rise lg:col-span-7" style={{ animationDelay: '0.12s' }} id="trust-gate">
            <TrustGateOrbit />
          </div>
        </div>
      </section>

      {/* Trust flow band */}
      <section className="border-y border-[var(--ks-line)]" style={{ background: 'var(--ks-surface-blue)' }}>
        <div className="mx-auto grid max-w-[1200px] gap-6 px-4 py-10 sm:px-6 md:grid-cols-3">
          {[
            { t: 'HEALTHY', d: 'PASS → EXECUTE', c: 'var(--ks-pass)' },
            { t: 'DEGRADED', d: 'HOLD → ALERT ONLY', c: 'var(--ks-hold)' },
            { t: 'UNHEALTHY', d: 'BLOCK → MANUAL', c: 'var(--ks-block)' },
          ].map((row) => (
            <div key={row.t} className="border-l-4 pl-4" style={{ borderColor: row.c }}>
              <p className="ks-label">Signal</p>
              <p className="ks-display mt-1 text-2xl" style={{ color: 'var(--ks-ink)' }}>
                {row.t}
              </p>
              <p className="ks-mono mt-2 text-xs" style={{ color: row.c }}>
                {row.d}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Uneven feature bands */}
      <section className="mx-auto max-w-[1200px] space-y-16 px-4 py-16 sm:px-6">
        <Band
          label="01 · Autopilot"
          title="Budget moves only through the gate."
          body="Advisory, Soft-Block, or Hard-Block. Every action shows evidence, confidence, and a human override path."
          accent="cobalt"
        >
          <div className="grid gap-3 sm:grid-cols-2">
            {['Auditable', 'Explainable', 'Reversible', 'Human approval'].map((x) => (
              <div key={x} className="ks-panel-lime px-4 py-3 text-sm font-medium">
                {x}
              </div>
            ))}
          </div>
        </Band>

        <Band
          label="02 · Measurement"
          title="Act on Meta. Verify with GA4."
          body="GA4 is an independent read-only baseline. GTM deploys web and server-side tags. Neither is an ad channel."
          accent="cyan"
          reverse
        >
          <div className="space-y-3">
            <Lane name="Meta" items={['Campaigns', 'Budget', 'Audiences', 'Pacing']} hot />
            <div className="ks-mono text-center text-[10px]" style={{ color: 'var(--ks-muted)' }}>
              ── verification bridge ──
            </div>
            <Lane name="GA4 · read-only" items={['Revenue baseline', 'Conversions', 'Variance']} />
            <Lane name="GTM" items={['Web tags', 'Server tags', 'Pixel / CAPI']} />
          </div>
        </Band>

        <Band
          label="03 · CDP"
          title="Identity that feeds the Trust Engine."
          body="EMQ, consent, and resolved profiles contribute to signal health — never raw PII on screen."
          accent="lime"
        >
          <div
            className="relative h-40 overflow-hidden border border-[var(--ks-line)]"
            style={{ background: 'var(--ks-surface)' }}
          >
            <svg viewBox="0 0 400 160" className="h-full w-full" aria-hidden>
              <circle cx="200" cy="80" r="28" fill="var(--ks-cobalt-soft)" stroke="var(--ks-cobalt)" />
              {[40, 360, 80, 320, 120, 280].map((x, i) => (
                <g key={x}>
                  <line
                    x1={x}
                    y1={i % 2 ? 30 : 130}
                    x2="200"
                    y2="80"
                    stroke="var(--ks-cyan)"
                    strokeWidth="1"
                    opacity="0.5"
                  />
                  <circle
                    cx={x}
                    cy={i % 2 ? 30 : 130}
                    r="8"
                    fill="var(--ks-surface)"
                    stroke="var(--ks-lime)"
                  />
                </g>
              ))}
              <text
                x="200"
                y="85"
                textAnchor="middle"
                fill="var(--ks-ink)"
                style={{ fontFamily: 'var(--ks-font-mono)', fontSize: 12 }}
              >
                EMQ 87
              </text>
            </svg>
          </div>
        </Band>
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-[1200px] px-4 pb-20 sm:px-6">
        <div
          className="relative overflow-hidden px-6 py-12 sm:px-10"
          style={{ background: 'var(--ks-cobalt)', color: '#fff' }}
        >
          <div
            className="pointer-events-none absolute -right-10 -top-10 h-48 w-48 rounded-full opacity-30"
            style={{ background: 'var(--ks-lime)' }}
          />
          <p className="ks-label" style={{ color: 'rgba(255,255,255,0.7)' }}>
            Next
          </p>
          <h2 className="ks-display mt-2 text-4xl sm:text-5xl">See the command surface.</h2>
          <p className="mt-3 max-w-lg text-sm opacity-90">
            Signal Weather, Trust Gate Decision, Campaign Pulse, Evidence Stream — design sample
            with labeled mock data.
          </p>
          <Link
            to="/studio-preview/kinetic/dashboard"
            className="mt-6 inline-flex items-center gap-2 bg-[var(--ks-lime)] px-5 py-3 text-sm font-semibold no-underline"
            style={{ color: '#101828' }}
          >
            Open dashboard preview →
          </Link>
        </div>
      </section>
    </div>
  )
}

function Band({
  label,
  title,
  body,
  children,
  reverse,
  accent,
}: {
  label: string
  title: string
  body: string
  children: ReactNode
  reverse?: boolean
  accent: 'cobalt' | 'cyan' | 'lime'
}) {
  const color =
    accent === 'cobalt' ? 'var(--ks-cobalt)' : accent === 'cyan' ? 'var(--ks-cyan)' : 'var(--ks-lime)'
  return (
    <div className={`grid gap-8 lg:grid-cols-12 ${reverse ? 'lg:[&>*:first-child]:order-2' : ''}`}>
      <div className="lg:col-span-5">
        <p className="ks-label" style={{ color }}>
          {label}
        </p>
        <h2 className="ks-display mt-2 text-3xl sm:text-4xl" style={{ color: 'var(--ks-ink)' }}>
          {title}
        </h2>
        <p className="mt-3 text-sm leading-relaxed" style={{ color: 'var(--ks-muted)' }}>
          {body}
        </p>
      </div>
      <div className="lg:col-span-7">{children}</div>
    </div>
  )
}

function Lane({ name, items, hot }: { name: string; items: string[]; hot?: boolean }) {
  return (
    <div
      className="flex flex-wrap items-center gap-2 border border-[var(--ks-line)] px-3 py-2"
      style={{ background: hot ? 'var(--ks-surface-blue)' : 'var(--ks-surface)' }}
    >
      <span className="ks-mono text-[10px] font-semibold" style={{ color: 'var(--ks-cobalt)' }}>
        {name}
      </span>
      {items.map((i) => (
        <span key={i} className="text-xs" style={{ color: 'var(--ks-muted)' }}>
          · {i}
        </span>
      ))}
    </div>
  )
}

export default KineticMarketingHome
