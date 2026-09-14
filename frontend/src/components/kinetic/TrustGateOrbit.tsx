import { useEffect, useState } from 'react'

type GateState = 'EXECUTE' | 'HOLD' | 'MANUAL REQUIRED'

const CYCLE: { state: GateState; health: string; reason: string }[] = [
  {
    state: 'EXECUTE',
    health: 'HEALTHY',
    reason: 'Freshness 6m · EMQ 88 · variance within band',
  },
  {
    state: 'HOLD',
    health: 'DEGRADED',
    reason: 'Conversion lag 11h exceeds evidence window',
  },
  {
    state: 'MANUAL REQUIRED',
    health: 'UNHEALTHY',
    reason: 'Platform vs GA4 variance breached · block spend moves',
  },
]

/**
 * Living Trust Gate — hero / overview visualization (design sample).
 */
export function TrustGateOrbit() {
  const [idx, setIdx] = useState(0)
  const current = CYCLE[idx]

  useEffect(() => {
    const reduced =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced) return
    const t = window.setInterval(() => setIdx((i) => (i + 1) % CYCLE.length), 4200)
    return () => window.clearInterval(t)
  }, [])

  const badge =
    current.state === 'EXECUTE'
      ? 'ks-badge-pass'
      : current.state === 'HOLD'
        ? 'ks-badge-hold'
        : 'ks-badge-block'

  return (
    <div
      className="relative overflow-hidden ks-panel"
      style={{ minHeight: 420, boxShadow: 'var(--ks-glow)' }}
      aria-live="polite"
    >
      <div className="ks-ribbon" />
      <div className="relative p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="ks-label">Trust Gate · live sample</p>
            <p className="ks-display mt-1 text-2xl sm:text-3xl" style={{ color: 'var(--ks-ink)' }}>
              Signal → Gate → Decision
            </p>
          </div>
          <span className={`ks-badge ${badge}`}>
            <span className="ks-signal-dot" style={{ background: 'currentColor' }} />
            {current.state}
          </span>
        </div>

        <div className="relative mx-auto mt-6 aspect-square max-w-[340px]">
          <svg viewBox="0 0 200 200" className="h-full w-full" aria-hidden>
            <circle
              cx="100"
              cy="100"
              r="88"
              fill="none"
              stroke="var(--ks-line)"
              strokeWidth="1"
            />
            <circle
              cx="100"
              cy="100"
              r="68"
              fill="none"
              stroke="var(--ks-orbit)"
              strokeWidth="1.5"
              strokeDasharray="4 6"
              className="ks-orbit-spin"
              style={{ transformOrigin: '100px 100px' }}
            />
            <circle
              cx="100"
              cy="100"
              r="48"
              fill="var(--ks-cobalt-soft)"
              stroke="var(--ks-cobalt)"
              strokeWidth="2"
            />
            {/* Signal trails */}
            {[0, 72, 144, 216, 288].map((deg, i) => {
              const rad = (deg * Math.PI) / 180
              const x2 = 100 + Math.cos(rad) * 82
              const y2 = 100 + Math.sin(rad) * 82
              return (
                <line
                  key={deg}
                  x1="100"
                  y1="100"
                  x2={x2}
                  y2={y2}
                  stroke={i % 2 === 0 ? 'var(--ks-cyan)' : 'var(--ks-lime)'}
                  strokeWidth="1.2"
                  className="ks-trail-line"
                  style={{ animationDelay: `${i * 0.35}s` }}
                />
              )
            })}
            <text
              x="100"
              y="94"
              textAnchor="middle"
              fill="var(--ks-ink)"
              style={{ fontFamily: 'var(--ks-font-mono)', fontSize: 11 }}
            >
              {current.health}
            </text>
            <text
              x="100"
              y="114"
              textAnchor="middle"
              fill="var(--ks-cobalt)"
              style={{ fontFamily: 'var(--ks-font-display)', fontSize: 18, fontWeight: 700 }}
            >
              72
            </text>
          </svg>

          {/* Orbiting evidence chips */}
          <EvidenceChip className="absolute left-2 top-8" label="Freshness" value="6m" />
          <EvidenceChip className="absolute right-0 top-16" label="EMQ" value="88" />
          <EvidenceChip className="absolute bottom-10 left-4" label="Variance" value="±4%" />
          <EvidenceChip className="absolute bottom-6 right-2" label="Override" value="armed" />
        </div>

        <div className="mt-2 border-t border-[var(--ks-line)] pt-4">
          <p className="ks-label">Why this decision</p>
          <p className="mt-1 text-sm" style={{ color: 'var(--ks-muted)' }}>
            {current.reason}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {['Event freshness', 'Conversion integrity', 'Platform↔GA4', 'CDP EMQ', 'Pacing'].map(
              (c) => (
                <span
                  key={c}
                  className="ks-mono border border-[var(--ks-line)] px-2 py-1 text-[10px]"
                  style={{ background: 'var(--ks-surface-blue)' }}
                >
                  {c}
                </span>
              ),
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function EvidenceChip({
  className,
  label,
  value,
}: {
  className?: string
  label: string
  value: string
}) {
  return (
    <div
      className={className}
      style={{
        background: 'var(--ks-raised)',
        border: '1px solid var(--ks-line)',
        padding: '6px 10px',
        boxShadow: 'var(--ks-shadow)',
      }}
    >
      <p className="ks-label">{label}</p>
      <p className="ks-mono text-xs font-semibold" style={{ color: 'var(--ks-ink)' }}>
        {value}
      </p>
    </div>
  )
}
