type Mode = 'light' | 'dark'

/**
 * Signal-orbit theme control (not a generic sun/moon icon alone).
 */
export function KineticThemeToggle({
  mode,
  onToggle,
}: {
  mode: Mode
  onToggle: () => void
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="inline-flex items-center gap-2 border border-[var(--ks-line)] px-2.5 py-1.5 text-[11px]"
      style={{ background: 'var(--ks-surface)' }}
      aria-label={`Switch to ${mode === 'light' ? 'dark' : 'light'} theme`}
    >
      <span
        className="relative flex h-4 w-4 items-center justify-center rounded-full border"
        style={{ borderColor: 'var(--ks-cobalt)' }}
        aria-hidden
      >
        <span
          className="ks-signal-dot absolute"
          style={{
            width: 5,
            height: 5,
            background: mode === 'dark' ? 'var(--ks-lime)' : 'var(--ks-cobalt)',
          }}
        />
      </span>
      <span className="ks-mono uppercase tracking-wider" style={{ color: 'var(--ks-muted)' }}>
        {mode === 'light' ? 'Day panel' : 'Night observatory'}
      </span>
    </button>
  )
}
