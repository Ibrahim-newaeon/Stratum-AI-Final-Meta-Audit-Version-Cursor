import { Link, NavLink, Outlet } from 'react-router-dom'
import { KineticThemeToggle } from '@/components/kinetic/KineticThemeToggle'

type Props = {
  mode: 'light' | 'dark'
  onToggleMode: () => void
}

const NAV = [
  { to: '/studio-preview/kinetic', label: 'Home', end: true },
  { to: '/studio-preview/kinetic/dashboard', label: 'Dashboard' },
  { to: '/how-it-works', label: 'How it works' },
  { to: '/pricing', label: 'Pricing' },
]

/**
 * Marketing chrome for Kinetic Signal Observatory preview.
 */
export function KineticMarketingShell({ mode, onToggleMode }: Props) {
  return (
    <div className={`ks-root ${mode === 'dark' ? 'ks-dark' : 'ks-light'}`} data-ks-mode={mode}>
      <div className="ks-ribbon" />
      <header className="sticky top-0 z-30 border-b border-[var(--ks-line)] backdrop-blur-md"
        style={{ background: 'color-mix(in srgb, var(--ks-canvas) 88%, transparent)' }}
      >
        <div className="mx-auto flex max-w-[1200px] items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link to="/studio-preview/kinetic" className="flex items-center gap-2 no-underline">
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
              <p className="ks-label mt-0.5">Kinetic Signal Observatory · preview</p>
            </div>
          </Link>

          <nav className="hidden items-center gap-5 md:flex" aria-label="Marketing">
            {NAV.map((item) => (
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

      <Outlet />

      <footer className="mt-16 border-t border-[var(--ks-line)] px-6 py-8">
        <div className="mx-auto flex max-w-[1200px] flex-wrap items-center justify-between gap-3">
          <p className="ks-mono text-[11px]" style={{ color: 'var(--ks-muted)' }}>
            Design preview · sample data · Meta activation only · GA4/GTM = measurement
          </p>
          <Link
            to="/studio-preview/kinetic/dashboard"
            className="text-sm font-medium no-underline"
            style={{ color: 'var(--ks-cobalt)' }}
          >
            Open dashboard preview →
          </Link>
        </div>
      </footer>
    </div>
  )
}
