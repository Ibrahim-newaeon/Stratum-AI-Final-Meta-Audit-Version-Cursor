import type { ReactNode } from 'react'
import { Link, NavLink, Outlet } from 'react-router-dom'
import { KineticThemeToggle } from '@/components/kinetic/KineticThemeToggle'
import { useTheme } from '@/contexts/ThemeContext'

type ShellProps = {
  mode: 'light' | 'dark'
  onToggleMode: () => void
  /** When true, shows preview labeling and preview dashboard link. */
  preview?: boolean
  children?: ReactNode
}

/**
 * Kinetic marketing chrome — preview router Outlet or live children (home/auth).
 */
export function KineticMarketingShell({ mode, onToggleMode, preview, children }: ShellProps) {
  const home = preview ? '/studio-preview/kinetic' : '/'
  const dash = preview ? '/studio-preview/kinetic/dashboard' : '/dashboard/overview'
  const nav = [
    { to: home, label: 'Home', end: true },
    { to: dash, label: 'Dashboard' },
    { to: '/pricing', label: 'Pricing' },
  ]

  return (
    <div className={`ks-root ${mode === 'dark' ? 'ks-dark' : 'ks-light'}`} data-ks-mode={mode}>
      <div className="ks-ribbon" />
      <header
        className="sticky top-0 z-30 border-b border-[var(--ks-line)] backdrop-blur-md"
        style={{ background: 'color-mix(in srgb, var(--ks-canvas) 88%, transparent)' }}
      >
        <div className="mx-auto flex max-w-[1200px] items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link to={home} className="flex items-center gap-2 no-underline">
            <span
              className="relative flex h-8 w-8 items-center justify-center"
              style={{ background: 'var(--ks-cobalt)' }}
              aria-hidden
            >
              <span className="ks-signal-dot" style={{ background: 'var(--ks-lime)' }} />
            </span>
            <div>
              <p className="ks-display text-lg leading-none" style={{ color: 'var(--ks-ink)' }}>
                StratumAI
              </p>
              <p className="ks-label mt-0.5">
                {preview ? 'Kinetic Signal Observatory · preview' : 'Kinetic Signal Observatory'}
              </p>
            </div>
          </Link>

          <nav className="hidden items-center gap-5 md:flex" aria-label="Marketing">
            {nav.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `text-sm no-underline ${isActive ? 'font-semibold' : ''}`
                }
                style={{ color: 'var(--ks-muted)' }}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="flex items-center gap-2 sm:gap-3">
            <KineticThemeToggle mode={mode} onToggle={onToggleMode} />
            <Link to="/login" className="ks-btn-secondary hidden sm:inline-flex !py-2 !text-xs">
              Sign in
            </Link>
            <Link to="/signup" className="ks-btn-primary !py-2 !text-xs">
              Start free trial
            </Link>
          </div>
        </div>
      </header>

      {children ?? <Outlet />}

      <footer className="mt-16 border-t border-[var(--ks-line)] px-6 py-8">
        <div className="mx-auto flex max-w-[1200px] flex-wrap items-center justify-between gap-3">
          <p className="ks-mono text-[11px]" style={{ color: 'var(--ks-muted)' }}>
            Meta activation only · GA4/GTM = measurement · Trust Gate before spend moves
          </p>
          <Link
            to={dash}
            className="text-sm font-medium no-underline"
            style={{ color: 'var(--ks-cobalt)' }}
          >
            {preview ? 'Open dashboard preview →' : 'Open dashboard →'}
          </Link>
        </div>
      </footer>
    </div>
  )
}

/** Live marketing/auth wrapper — ThemeContext-driven Kinetic shell. */
export function KineticLiveMarketingShell({ children }: { children: ReactNode }) {
  const { resolvedTheme, setTheme } = useTheme()
  const mode = resolvedTheme === 'dark' ? 'dark' : 'light'
  const onToggleMode = () => setTheme(mode === 'dark' ? 'light' : 'dark')

  return (
    <KineticMarketingShell mode={mode} onToggleMode={onToggleMode}>
      {children}
    </KineticMarketingShell>
  )
}
