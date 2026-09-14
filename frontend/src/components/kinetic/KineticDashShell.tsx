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
import { ActivationBanner } from '@/components/activation/ActivationBanner'
import { useTheme } from '@/contexts/ThemeContext'
import { cn } from '@/lib/utils'

type NavItem = {
  to: string
  label: string
  icon: typeof LayoutDashboard
  end?: boolean
}

/** Live dashboard nav — Kinetic chrome mapped to real product routes. */
const LIVE_NAV: NavItem[] = [
  { to: '/dashboard/overview', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/dashboard/campaigns', label: 'Campaigns', icon: Zap },
  { to: '/dashboard/stratum', label: 'Trust Engine', icon: Shield },
  { to: '/dashboard/custom-autopilot-rules', label: 'Autopilot', icon: Radio },
  { to: '/dashboard/cdp', label: 'CDP', icon: Users },
  { to: '/dashboard/performance', label: 'Measurement', icon: Gauge },
  { to: '/dashboard/recommendations', label: 'Attribution', icon: Activity },
  { to: '/dashboard/assets', label: 'Assets', icon: Boxes },
  { to: '/dashboard/campaigns/connect', label: 'Integrations', icon: Cable },
  { to: '/dashboard/settings', label: 'Billing', icon: Wallet },
  { to: '/dashboard/settings', label: 'Settings', icon: Settings },
]

type Props = {
  mode: 'light' | 'dark'
  onToggleMode: () => void
  /** Route prefix for preview nav (`/studio-preview/kinetic`) or omit for live. */
  basePath?: string
  preview?: boolean
}

/**
 * Kinetic dashboard chrome — live (`/dashboard`) or design preview.
 */
export function KineticDashShell({ mode, onToggleMode, basePath, preview }: Props) {
  const isPreview = Boolean(preview || basePath)
  const base = basePath ?? '/dashboard'

  const nav: NavItem[] = isPreview
    ? [
        { to: `${base}/dashboard`, label: 'Overview', icon: LayoutDashboard, end: true },
        { to: `${base}/dashboard/campaigns`, label: 'Campaigns', icon: Zap },
        { to: `${base}/dashboard/trust-engine`, label: 'Trust Engine', icon: Shield },
        { to: `${base}/dashboard/autopilot`, label: 'Autopilot', icon: Radio },
        { to: `${base}/dashboard/cdp`, label: 'CDP', icon: Users },
        { to: `${base}/dashboard/measurement`, label: 'Measurement', icon: Gauge },
        { to: `${base}/dashboard/attribution`, label: 'Attribution', icon: Activity },
        { to: `${base}/dashboard/assets`, label: 'Assets', icon: Boxes },
        { to: `${base}/dashboard/integrations`, label: 'Integrations', icon: Cable },
        { to: `${base}/dashboard/billing`, label: 'Billing', icon: Wallet },
        { to: `${base}/dashboard/settings`, label: 'Settings', icon: Settings },
      ]
    : LIVE_NAV

  const homeHref = isPreview ? base : '/dashboard/overview'

  return (
    <div className={`ks-root flex min-h-screen ${mode === 'dark' ? 'ks-dark' : 'ks-light'}`}>
      <aside
        className="sticky top-0 flex h-screen w-[72px] shrink-0 flex-col items-center border-r border-[var(--ks-line)] py-4 lg:w-[220px] lg:items-stretch lg:px-3"
        style={{ background: 'var(--ks-surface)' }}
      >
        <NavLink to={homeHref} className="mb-6 flex items-center gap-2 px-1 no-underline lg:px-2">
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
            <p className="ks-label">{isPreview ? 'Preview' : 'Observatory'}</p>
          </div>
        </NavLink>

        <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto" aria-label="Dashboard">
          {nav.map((item) => {
            const Icon = item.icon
            return (
              <NavLink
                key={`${item.label}-${item.to}`}
                to={item.to}
                end={item.end}
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
            SIGNAL · TRUST GATE
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
          {!isPreview ? <ActivationBanner /> : null}
          <Outlet />
        </main>
      </div>
    </div>
  )
}

/** Live `/dashboard` layout — Kinetic chrome + ThemeContext. */
export default function KineticLiveAppShell() {
  const { resolvedTheme, setTheme } = useTheme()
  const mode = resolvedTheme === 'dark' ? 'dark' : 'light'
  const onToggleMode = () => setTheme(mode === 'dark' ? 'light' : 'dark')

  return <KineticDashShell mode={mode} onToggleMode={onToggleMode} />
}
