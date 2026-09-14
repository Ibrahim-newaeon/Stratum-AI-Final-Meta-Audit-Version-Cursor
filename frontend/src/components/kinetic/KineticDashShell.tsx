import { NavLink, Outlet } from 'react-router-dom'
import {
  Activity,
  Boxes,
  Cable,
  Gauge,
  LayoutDashboard,
  Radio,
  Settings,
  Shield,
  Users,
  Wallet,
  Zap,
} from 'lucide-react'
import { KineticThemeToggle } from '@/components/kinetic/KineticThemeToggle'
import { cn } from '@/lib/utils'

type Props = {
  mode: 'light' | 'dark'
  onToggleMode: () => void
}

const NAV = [
  { to: 'dashboard', label: 'Overview', icon: LayoutDashboard },
  { to: 'campaigns', label: 'Campaigns', icon: Zap },
  { to: 'trust-engine', label: 'Trust Engine', icon: Shield },
  { to: 'autopilot', label: 'Autopilot', icon: Radio },
  { to: 'cdp', label: 'CDP', icon: Users },
  { to: 'measurement', label: 'Measurement', icon: Gauge },
  { to: 'attribution', label: 'Attribution', icon: Activity },
  { to: 'assets', label: 'Assets', icon: Boxes },
  { to: 'integrations', label: 'Integrations', icon: Cable },
  { to: 'billing', label: 'Billing', icon: Wallet },
  { to: 'settings', label: 'Settings', icon: Settings },
]

/**
 * Narrow command rail + workspace for Kinetic dashboard preview.
 */
export function KineticDashShell({ mode, onToggleMode }: Props) {
  const base = '/studio-preview/kinetic'

  return (
    <div className={`ks-root flex min-h-screen ${mode === 'dark' ? 'ks-dark' : 'ks-light'}`}>
      <aside
        className="sticky top-0 flex h-screen w-[72px] shrink-0 flex-col items-center border-r border-[var(--ks-line)] py-4 lg:w-[220px] lg:items-stretch lg:px-3"
        style={{ background: 'var(--ks-surface)' }}
      >
        <NavLink to={base} className="mb-6 flex items-center gap-2 px-1 no-underline lg:px-2">
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center"
            style={{ background: 'var(--ks-cobalt)' }}
          >
            <span className="ks-signal-dot" style={{ background: 'var(--ks-lime)' }} />
          </span>
          <div className="hidden lg:block">
            <p className="ks-display text-base" style={{ color: 'var(--ks-ink)' }}>
              Stratum
            </p>
            <p className="ks-label">Observatory</p>
          </div>
        </NavLink>

        <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto" aria-label="Dashboard">
          {NAV.map((item) => {
            const Icon = item.icon
            const href =
              item.to === 'dashboard' ? `${base}/dashboard` : `${base}/dashboard/${item.to}`
            return (
              <NavLink
                key={item.to}
                to={href}
                end={item.to === 'dashboard'}
                className={({ isActive }) =>
                  cn(
                    'relative flex items-center gap-2.5 px-2 py-2 text-[13px] no-underline transition-colors',
                    isActive
                      ? 'font-semibold text-[var(--ks-ink)] bg-[var(--ks-cobalt-soft)]'
                      : 'text-[var(--ks-muted)]',
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    {isActive ? (
                      <span
                        className="absolute left-0 top-1 bottom-1 w-[3px] bg-[var(--ks-lime)]"
                        aria-hidden
                      />
                    ) : null}
                    <Icon className="mx-auto h-4 w-4 shrink-0 lg:mx-0" />
                    <span className="hidden lg:inline">{item.label}</span>
                  </>
                )}
              </NavLink>
            )
          })}
        </nav>

        <div className="mt-3 hidden border-t border-[var(--ks-line)] pt-3 lg:block">
          <KineticThemeToggle mode={mode} onToggle={onToggleMode} />
          <p className="ks-mono mt-3 px-1 text-[10px]" style={{ color: 'var(--ks-pass)' }}>
            SIGNAL 72 · PASS
          </p>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header
          className="flex h-14 items-center justify-between border-b border-[var(--ks-line)] px-4 sm:px-6"
          style={{ background: 'var(--ks-canvas)' }}
        >
          <div>
            <p className="ks-label">Workspace · Meta revenue OS</p>
            <p className="text-sm font-semibold" style={{ color: 'var(--ks-ink)' }}>
              Kinetic Signal Observatory
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="ks-badge ks-badge-pass hidden sm:inline-flex">
              <span className="ks-signal-dot" style={{ background: 'var(--ks-pass)' }} />
              Autopilot armed
            </span>
            <div className="lg:hidden">
              <KineticThemeToggle mode={mode} onToggle={onToggleMode} />
            </div>
          </div>
        </header>
        <main className="flex-1 overflow-y-auto px-4 py-5 sm:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
