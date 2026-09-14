import type { ReactNode } from 'react'
import { AlertTriangle, ArrowRight, CheckCircle2, Clock3, PauseCircle } from 'lucide-react'
import { CdpIdentityGraphModule } from '@/components/luminous/CdpIdentityGraphModule'
import { TrustGatedAutopilotModule } from '@/components/luminous/TrustGatedAutopilotModule'
import { cn } from '@/lib/utils'

/**
 * Luminous Control — Command Overview (design sample).
 * Numbers are labeled samples for approval; wire to real APIs only after design sign-off.
 */
export function LuminousCommandOverview() {
  return (
    <div className="mx-auto max-w-[1280px] space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight text-[var(--lc-text)]">
            Command Overview
          </h1>
          <p className="mt-1 max-w-xl text-[13px] text-[var(--lc-muted)]">
            Trust-gated Meta operations in one grid — sample layout for design approval.
          </p>
        </div>
        <p className="rounded-[10px] border border-[var(--lc-border)] bg-[var(--lc-surface)] px-3 py-1.5 text-[11px] text-[var(--lc-muted)]">
          Sample data · not live metrics
        </p>
      </div>

      {/* KPI row — 12 col */}
      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard label="Signal health" value="72" suffix="PASS" tone="ok" hint="Gate ≥ 70" />
        <KpiCard label="Spend today" value="$4,280" tone="neutral" hint="Meta Ads (sample)" />
        <KpiCard label="ROAS" value="—" tone="warn" hint="Undefined until attribution sync" />
        <KpiCard label="Queue depth" value="6" tone="neutral" hint="3 hold · 2 approved · 1 applying" />
      </section>

      {/* Autopilot 8 + CDP 4 */}
      <section className="grid grid-cols-1 gap-4 xl:grid-cols-12">
        <div className="xl:col-span-8">
          <TrustGatedAutopilotModule />
        </div>
        <div className="xl:col-span-4">
          <CdpIdentityGraphModule />
        </div>
      </section>

      {/* Pacing + insights */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Pacing" subtitle="Daily budget vs delivery (sample)">
          <div className="space-y-3">
            {[
              { name: 'Prospecting · Catalog', pct: 68, status: 'On pace' },
              { name: 'Retargeting · 7d', pct: 91, status: 'Hot' },
              { name: 'WhatsApp · Lead', pct: 42, status: 'Under' },
            ].map((row) => (
              <div key={row.name}>
                <div className="mb-1 flex items-center justify-between text-[12px]">
                  <span className="text-[var(--lc-text)]">{row.name}</span>
                  <span className="text-[var(--lc-muted)]">
                    {row.pct}% · {row.status}
                  </span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-[var(--lc-surface-2)]">
                  <div
                    className="h-full rounded-full bg-[var(--lc-accent)]"
                    style={{ width: `${row.pct}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Signal notes" subtitle="What the gate is reading">
          <ul className="space-y-2.5">
            <Note ok>CAPI match quality holding above threshold</Note>
            <Note ok>Insights freshness &lt; 2h on primary ad account</Note>
            <Note warn>ROAS KPI withheld — attribution window not confirmed</Note>
            <Note hold>3 actions on Trust Hold — open dossier before override</Note>
          </ul>
        </Panel>
      </section>

      {/* Action queue */}
      <Panel title="Action Queue" subtitle="Approve only when the gate says PASS">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-[13px]">
            <thead>
              <tr className="border-b border-[var(--lc-border)] text-[11px] uppercase tracking-wider text-[var(--lc-muted)]">
                <th className="pb-2 pr-3 font-medium">Action</th>
                <th className="pb-2 pr-3 font-medium">Entity</th>
                <th className="pb-2 pr-3 font-medium">Gate</th>
                <th className="pb-2 pr-3 font-medium">Status</th>
                <th className="pb-2 font-medium">Next</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--lc-border)]">
              {QUEUE.map((row) => (
                <tr key={row.id} className="text-[var(--lc-text)]">
                  <td className="py-2.5 pr-3 font-medium">{row.action}</td>
                  <td className="py-2.5 pr-3 text-[var(--lc-muted)]">{row.entity}</td>
                  <td className="py-2.5 pr-3">
                    <GatePill score={row.score} gate={row.gate} />
                  </td>
                  <td className="py-2.5 pr-3">
                    <StatusPill status={row.status} />
                  </td>
                  <td className="py-2.5">
                    <span className="inline-flex items-center gap-1 text-[var(--lc-accent)]">
                      {row.next}
                      <ArrowRight className="h-3 w-3" />
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}

function KpiCard({
  label,
  value,
  suffix,
  tone,
  hint,
}: {
  label: string
  value: string
  suffix?: string
  tone: 'ok' | 'warn' | 'neutral'
  hint: string
}) {
  const valueColor =
    tone === 'ok'
      ? 'text-[var(--lc-ok)]'
      : tone === 'warn'
        ? 'text-[var(--lc-warn)]'
        : 'text-[var(--lc-text)]'
  return (
    <div className="rounded-[10px] border border-[var(--lc-border)] bg-[var(--lc-surface)] px-4 py-3 shadow-[var(--lc-shadow)]">
      <p className="text-[11px] uppercase tracking-wider text-[var(--lc-muted)]">{label}</p>
      <p className={cn('mt-1 text-[22px] font-semibold tabular-nums tracking-tight', valueColor)}>
        {value}
        {suffix ? (
          <span className="ml-2 text-[12px] font-medium text-[var(--lc-muted)]">{suffix}</span>
        ) : null}
      </p>
      <p className="mt-1 text-[11px] text-[var(--lc-muted)]">{hint}</p>
    </div>
  )
}

function Panel({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle: string
  children: ReactNode
}) {
  return (
    <section className="rounded-[10px] border border-[var(--lc-border)] bg-[var(--lc-surface)] p-4 shadow-[var(--lc-shadow)]">
      <div className="mb-3">
        <h2 className="text-[15px] font-semibold text-[var(--lc-text)]">{title}</h2>
        <p className="text-[12px] text-[var(--lc-muted)]">{subtitle}</p>
      </div>
      {children}
    </section>
  )
}

function Note({
  children,
  warn,
  hold,
}: {
  children: ReactNode
  ok?: boolean
  warn?: boolean
  hold?: boolean
}) {
  const Icon = hold ? PauseCircle : warn ? AlertTriangle : CheckCircle2
  const color = hold
    ? 'text-[var(--lc-hold)]'
    : warn
      ? 'text-[var(--lc-warn)]'
      : 'text-[var(--lc-ok)]'
  return (
    <li className="flex items-start gap-2 text-[13px] text-[var(--lc-text)]">
      <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', color)} />
      <span>{children}</span>
    </li>
  )
}

function GatePill({ score, gate }: { score: number; gate: 'PASS' | 'HOLD' | 'BLOCK' }) {
  const cls =
    gate === 'PASS'
      ? 'bg-[var(--lc-ok-soft)] text-[var(--lc-ok)]'
      : gate === 'HOLD'
        ? 'bg-[var(--lc-hold-soft)] text-[var(--lc-hold)]'
        : 'bg-[var(--lc-bad-soft)] text-[var(--lc-bad)]'
  return (
    <span className={cn('inline-flex rounded-[8px] px-2 py-0.5 text-[11px] font-medium', cls)}>
      {score} · {gate}
    </span>
  )
}

function StatusPill({ status }: { status: string }) {
  const Icon = status === 'Hold' ? PauseCircle : status === 'Applying' ? Clock3 : CheckCircle2
  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] text-[var(--lc-muted)]">
      <Icon className="h-3.5 w-3.5" />
      {status}
    </span>
  )
}

const QUEUE = [
  {
    id: '1',
    action: 'Pause ad set',
    entity: 'AS · Warm 14d',
    score: 74,
    gate: 'PASS' as const,
    status: 'Approved',
    next: 'Apply',
  },
  {
    id: '2',
    action: 'Budget +12%',
    entity: 'Camp · Catalog A',
    score: 58,
    gate: 'HOLD' as const,
    status: 'Hold',
    next: 'Review',
  },
  {
    id: '3',
    action: 'Bid floor ↑',
    entity: 'AS · Prospect',
    score: 71,
    gate: 'PASS' as const,
    status: 'Applying',
    next: 'Reconcile',
  },
  {
    id: '4',
    action: 'Pause creative',
    entity: 'Ad · Hook v3',
    score: 36,
    gate: 'BLOCK' as const,
    status: 'Hold',
    next: 'Manual',
  },
]
