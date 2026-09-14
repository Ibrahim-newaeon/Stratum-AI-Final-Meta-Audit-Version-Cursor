import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  Activity,
  Boxes,
  Brain,
  Cable,
  FlaskConical,
  LayoutDashboard,
  MessageSquare,
  Moon,
  Settings,
  Sun,
  Users,
  Zap,
} from 'lucide-react'
import { cn } from '@/lib/utils'

type LuminousAppShellProps = {
  mode: 'light' | 'dark'
  onToggleMode?: () => void
  preview?: boolean
}

const NAV = [
  {
    label: 'OPERATE',
    items: [
      { to: 'overview', label: 'Command', icon: LayoutDashboard },
      { to: 'campaigns', label: 'Campaigns', icon: Zap },
      { to: 'whatsapp', label: 'WhatsApp', icon: MessageSquare },
      { to: 'rules', label: 'Rules', icon: Activity },
    ],
  },
  {
    label: 'INTELLIGENCE',
    items: [
      { to: 'signals', label: 'Signals', icon: Brain },
      { to: 'attribution', label: 'Attribution', icon: FlaskConical },
      { to: 'cdp', label: 'CDP', icon: Users },
      { to: 'assets', label: 'Assets', icon: Boxes },
    ],
  },
  {
    label: 'WORKSPACE',
    items: [
      { to: 'connections', label: 'Connections', icon: Cable },
      { to: 'settings', label: 'Settings', icon: Settings },
    ],
  },
]

/**
 * Luminous Control app chrome — Pearl & Indigo / Navy & Periwinkle.
 * Preview-only until design is approved for full migration.
 */
export function LuminousAppShell({ mode, onToggleMode, preview }: LuminousAppShellProps) {
  const location = useLocation()
  const previewBase = '/studio-preview/luminous'

  return (
    <div
      className={cn(
        'lc-root flex min-h-screen',
        mode === 'dark' ? 'lc-dark' : 'lc-light',
      )}
      data-lc-mode={mode}
    >
      <aside className="lc-sidebar sticky top-0 flex h-screen w-[240px] shrink-0 flex-col px-3 py-5">
        <div className="mb-6 px-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--lc-accent)]">
            Stratum
          </p>
          <p className="mt-1 text-sm font-semibold text-[var(--lc-text)]">Command</p>
          {preview ? (
            <p className="mt-1 text-[10px] uppercase tracking-wider text-[var(--lc-muted)]">
              Design preview
            </p>
          ) : null}
        </div>

        <nav className="flex-1 space-y-5 overflow-y-auto">
          {NAV.map((group) => (
            <div key={group.label}>
              <p className="mb-1.5 px-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--lc-muted)]">
                {group.label}
              </p>
              <ul className="space-y-0.5">
                {group.items.map((item) => {
                  const Icon = item.icon
                  const href = preview ? `${previewBase}/${item.to}` : `/dashboard/${item.to}`
                  const active =
                    item.to === 'overview'
                      ? location.pathname.endsWith('/overview') ||
                        location.pathname.endsWith('/luminous') ||
                        location.pathname.endsWith('/luminous/')
                      : location.pathname.includes(`/${item.to}`)
                  return (
                    <li key={item.to}>
                      <NavLink
                        to={href}
                        end={item.to === 'overview'}
                        className={cn(
                          'flex items-center gap-2.5 rounded-[10px] px-2.5 py-2 text-[13px] transition-colors',
                          active
                            ? 'bg-[var(--lc-surface-2)] font-medium text-[var(--lc-text)]'
                            : 'text-[var(--lc-muted)] hover:bg-[var(--lc-surface-2)]/60 hover:text-[var(--lc-text)]',
                        )}
                      >
                        <Icon className="h-4 w-4 shrink-0 opacity-80" />
                        {item.label}
                      </NavLink>
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
        </nav>

        <div className="mt-4 space-y-2 border-t border-[var(--lc-border)] pt-4 px-1">
          {onToggleMode ? (
            <button
              type="button"
              onClick={onToggleMode}
              className="flex w-full items-center gap-2 rounded-[10px] px-2.5 py-2 text-[12px] text-[var(--lc-muted)] transition-colors hover:bg-[var(--lc-surface-2)] hover:text-[var(--lc-text)]"
            >
              {mode === 'dark' ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
              {mode === 'dark' ? 'Pearl mode' : 'Navy mode'}
            </button>
          ) : null}
          <div className="rounded-[10px] bg-[var(--lc-surface-2)] px-2.5 py-2">
            <p className="text-[10px] uppercase tracking-wider text-[var(--lc-muted)]">Signal</p>
            <p className="mt-0.5 text-sm font-semibold tabular-nums text-[var(--lc-ok)]">72 · PASS</p>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="lc-topbar sticky top-0 z-20 flex h-14 items-center justify-between px-6">
          <div>
            <p className="text-[11px] uppercase tracking-[0.14em] text-[var(--lc-muted)]">
              Luminous Control
            </p>
            <p className="text-sm font-medium text-[var(--lc-text)]">Meta revenue OS</p>
          </div>
          <div className="flex items-center gap-3 text-[12px] text-[var(--lc-muted)]">
            <span className="hidden sm:inline">Trust gate armed</span>
            <span className="inline-flex items-center gap-1.5 rounded-[10px] bg-[var(--lc-ok-soft)] px-2.5 py-1 text-[var(--lc-ok)]">
              <span className="h-1.5 w-1.5 rounded-full bg-[var(--lc-ok)]" />
              Live
            </span>
          </div>
        </header>
        <main className="flex-1 overflow-y-auto px-6 py-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
