import { useParams } from 'react-router-dom'

const LABELS: Record<string, string> = {
  campaigns: 'Campaigns',
  whatsapp: 'WhatsApp',
  rules: 'Rules',
  signals: 'Signals',
  attribution: 'Attribution',
  cdp: 'CDP',
  assets: 'Assets',
  connections: 'Connections',
  settings: 'Settings',
}

/** Placeholder for secondary nav items in the Luminous design preview. */
export default function LuminousPlaceholder() {
  const { section } = useParams()
  const label = (section && LABELS[section]) || 'Module'

  return (
    <div className="mx-auto max-w-lg rounded-[10px] border border-[var(--lc-border)] bg-[var(--lc-surface)] p-8 text-center shadow-[var(--lc-shadow)]">
      <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--lc-accent)]">
        Design preview
      </p>
      <h1 className="mt-2 text-xl font-semibold text-[var(--lc-text)]">{label}</h1>
      <p className="mt-2 text-[13px] text-[var(--lc-muted)]">
        Command Overview is the approval surface. This module ships after design sign-off.
      </p>
    </div>
  )
}
