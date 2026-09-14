/**
 * Evidence Room app shell — Operator taxonomy (Operate / Intelligence / Workspace)
 */

import { FormEvent, useMemo, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  Bell,
  ChevronLeft,
  ChevronRight,
  HelpCircle,
  LogOut,
  Menu,
  Moon,
  Search,
  Sun,
} from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { useTheme } from '@/contexts/ThemeContext';
import { ActivationBanner } from '@/components/activation/ActivationBanner';
import { CommandPalette } from '@/components/ui/command-palette';
import { cn } from '@/lib/utils';

type NavItem = { name: string; href: string };

/** Full operator taxonomy from the Evidence Room prompt */
const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: 'Operate',
    items: [
      { name: 'Overview', href: '/dashboard/overview' },
      { name: 'Custom Dashboard', href: '/dashboard/dashboard' },
      { name: 'Campaigns', href: '/dashboard/campaigns' },
      { name: 'Autopilot', href: '/dashboard/custom-autopilot-rules' },
      { name: 'Audiences', href: '/dashboard/cdp/audience-sync' },
      { name: 'Trust Engine', href: '/dashboard/stratum' },
      { name: 'Pacing', href: '/dashboard/performance' },
      { name: 'Rules', href: '/dashboard/rules' },
      { name: 'A/B Testing', href: '/dashboard/campaigns' },
      { name: 'Profit & ROAS', href: '/dashboard/performance' },
    ],
  },
  {
    title: 'Intelligence',
    items: [
      { name: 'CDP', href: '/dashboard/cdp' },
      { name: 'Attribution', href: '/dashboard/cdp' },
      { name: 'Reporting', href: '/dashboard/custom-reports' },
      { name: 'Knowledge Graph', href: '/dashboard/knowledge-graph' },
      { name: 'AI Insights', href: '/dashboard/knowledge-graph/insights' },
      { name: 'AI Recommendations', href: '/dashboard/recommendations' },
      { name: 'Cohort Analysis', href: '/dashboard/cdp' },
      { name: 'Funnel Analysis', href: '/dashboard/cdp/funnels' },
      { name: 'Predictions', href: '/dashboard/predictions' },
      { name: 'Benchmarks', href: '/dashboard/benchmarks' },
      { name: 'Anomalies', href: '/dashboard/emq-dashboard' },
      { name: 'Model Explainability', href: '/dashboard/predictions' },
      { name: 'SQL Editor', href: '/dashboard/cdp' },
    ],
  },
  {
    title: 'Workspace',
    items: [
      { name: 'Integrations', href: '/dashboard/campaigns/connect' },
      { name: 'Meta Setup', href: '/dashboard/activation' },
      { name: 'WhatsApp', href: '/dashboard/whatsapp' },
      { name: 'CAPI Setup', href: '/dashboard/capi-setup' },
      { name: 'Assets', href: '/dashboard/assets' },
      { name: 'Audit Log', href: '/dashboard/settings' },
      { name: 'Compliance', href: '/dashboard/data-quality' },
      { name: 'Data & Privacy', href: '/dashboard/data-quality' },
      { name: 'API Keys', href: '/dashboard/settings' },
      { name: 'Settings', href: '/dashboard/settings' },
      { name: 'Team', href: '/dashboard/settings' },
      { name: 'Billing', href: '/dashboard/settings' },
    ],
  },
];

function isActive(pathname: string, href: string) {
  if (href === '/dashboard/overview') {
    return pathname === '/dashboard/overview' || pathname === '/dashboard';
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

function crumbs(pathname: string) {
  if (pathname.includes('/activation')) return 'Dashboard / Meta Setup';
  if (pathname.includes('/campaigns')) return 'Dashboard / Campaigns';
  if (pathname.includes('/rules')) return 'Dashboard / Rules';
  return 'Dashboard / Overview';
}

export default function EvidenceAppShell() {
  const { user, logout } = useAuth();
  const { resolvedTheme, setTheme } = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [search, setSearch] = useState('');

  const initials = useMemo(() => {
    const name = user?.name || user?.email || 'U';
    return name
      .split(/\s+/)
      .map((p) => p[0])
      .join('')
      .slice(0, 2)
      .toUpperCase();
  }, [user]);

  const onSearch = (e: FormEvent) => {
    e.preventDefault();
  };

  const Sidebar = (
    <aside
      className="flex h-full flex-col border-r"
      style={{
        width: collapsed ? 'var(--er-sidebar-collapsed)' : 'var(--er-sidebar-width)',
        background: 'var(--er-surface)',
        borderColor: 'var(--er-border)',
        transition: prefersReduced() ? 'none' : 'width 180ms ease',
      }}
    >
      <div
        className="flex items-center gap-3 border-b px-4 py-4"
        style={{ borderColor: 'var(--er-border)' }}
      >
        <div
          className="flex h-8 w-8 shrink-0 items-center justify-center text-xs font-semibold"
          style={{
            background: 'var(--er-accent)',
            color: 'var(--er-accent-contrast)',
            borderRadius: 'var(--er-radius)',
          }}
        >
          S
        </div>
        {!collapsed && (
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold" style={{ color: 'var(--er-text)' }}>
              StratumAI
            </div>
            <div className="er-label">Evidence room</div>
          </div>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-3" aria-label="Primary">
        {NAV_GROUPS.map((group) => (
          <div key={group.title} className="mb-5">
            {!collapsed && <div className="er-label mb-2 px-3">{group.title}</div>}
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = isActive(location.pathname, item.href);
                return (
                  <li key={`${group.title}-${item.name}`}>
                    <NavLink
                      to={item.href}
                      onClick={() => setMobileOpen(false)}
                      title={item.name}
                      className={cn('relative flex items-center gap-2 px-3 py-1.5 text-[13px]')}
                      style={{
                        color: active ? 'var(--er-text)' : 'var(--er-muted)',
                        background: active ? 'var(--er-surface-2)' : 'transparent',
                        fontWeight: active ? 500 : 400,
                        borderRadius: 'var(--er-radius)',
                      }}
                    >
                      {active && (
                        <span
                          className="absolute bottom-1 left-0 top-1 w-[2px]"
                          style={{ background: 'var(--er-accent)' }}
                          aria-hidden
                        />
                      )}
                      {!collapsed ? (
                        item.name
                      ) : (
                        <span className="mx-auto text-[10px] font-medium">{item.name.slice(0, 2)}</span>
                      )}
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="border-t p-3" style={{ borderColor: 'var(--er-border)' }}>
        <button
          type="button"
          className="mb-3 flex w-full items-center justify-center gap-2 border px-2 py-2 text-xs"
          style={{
            borderColor: 'var(--er-border)',
            color: 'var(--er-muted)',
            borderRadius: 'var(--er-radius)',
          }}
          onClick={() => setCollapsed((c) => !c)}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        >
          {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
          {!collapsed && 'Collapse'}
        </button>
        <div className="flex items-center gap-2">
          <div
            className="flex h-8 w-8 shrink-0 items-center justify-center text-[10px] font-semibold"
            style={{
              background: 'var(--er-surface-2)',
              color: 'var(--er-accent)',
              borderRadius: 'var(--er-radius)',
            }}
          >
            {initials}
          </div>
          {!collapsed && (
            <>
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium">{user?.name || 'User'}</div>
                <div className="truncate text-[10px]" style={{ color: 'var(--er-muted)' }}>
                  {user?.email || 'Workspace'}
                </div>
              </div>
              <button
                type="button"
                onClick={() => logout()}
                aria-label="Sign out"
                style={{ color: 'var(--er-muted)' }}
              >
                <LogOut size={14} />
              </button>
            </>
          )}
        </div>
      </div>
    </aside>
  );

  return (
    <div className="er-shell flex h-screen overflow-hidden">
      <div className="hidden lg:flex">{Sidebar}</div>

      {mobileOpen && (
        <div className="fixed inset-0 z-50 flex lg:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-black/40"
            aria-label="Close menu"
            onClick={() => setMobileOpen(false)}
          />
          <div className="relative z-10 h-full shadow-lg">{Sidebar}</div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header
          className="flex items-center gap-3 border-b px-4 py-2.5"
          style={{ background: 'var(--er-surface)', borderColor: 'var(--er-border)' }}
        >
          <button
            type="button"
            className="lg:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
          >
            <Menu size={18} />
          </button>
          <div className="hidden text-xs sm:block" style={{ color: 'var(--er-muted)' }}>
            {crumbs(location.pathname)}
          </div>
          <form onSubmit={onSearch} className="mx-auto w-full max-w-md flex-1">
            <label className="relative block">
              <Search
                size={14}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"
                style={{ color: 'var(--er-muted)' }}
              />
              <input
                className="w-full border py-2 pl-9 pr-14 text-sm"
                style={{
                  background: 'var(--er-bg)',
                  borderColor: 'var(--er-border)',
                  color: 'var(--er-text)',
                  borderRadius: 'var(--er-radius)',
                }}
                placeholder="Search decisions, campaigns, evidence…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <kbd
                className="er-mono absolute right-2 top-1/2 hidden -translate-y-1/2 border px-1.5 py-0.5 text-[10px] sm:inline"
                style={{ borderColor: 'var(--er-border)', color: 'var(--er-muted)' }}
              >
                ⌘K
              </kbd>
            </label>
          </form>
          <div className="flex items-center gap-1">
            <button
              type="button"
              className="border p-2"
              style={{
                borderColor: 'var(--er-border)',
                borderRadius: 'var(--er-radius)',
                color: 'var(--er-muted)',
              }}
              onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
              aria-label="Toggle theme"
            >
              {resolvedTheme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <button
              type="button"
              className="border p-2"
              style={{
                borderColor: 'var(--er-border)',
                borderRadius: 'var(--er-radius)',
                color: 'var(--er-muted)',
              }}
              aria-label="Help"
              onClick={() => navigate('/docs')}
            >
              <HelpCircle size={16} />
            </button>
            <button
              type="button"
              className="border p-2"
              style={{
                borderColor: 'var(--er-border)',
                borderRadius: 'var(--er-radius)',
                color: 'var(--er-muted)',
              }}
              aria-label="Notifications"
            >
              <Bell size={16} />
            </button>
          </div>
        </header>

        <ActivationBanner />
        <main className="flex-1 overflow-y-auto" style={{ background: 'var(--er-bg)' }}>
          <Outlet />
        </main>
      </div>
      <CommandPalette />
    </div>
  );
}

function prefersReduced() {
  if (typeof window === 'undefined') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}
