/**
 * Studio App Shell — Pearl & Indigo / Navy & Periwinkle
 * Layout matches docs/design concept boards (narrow sidebar + top search bar).
 */

import { FormEvent, useMemo, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  Bell,
  FolderKanban,
  LayoutGrid,
  Link2,
  LogOut,
  Menu,
  Moon,
  Package,
  Search,
  Settings,
  Sun,
  Users,
} from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { useTheme } from '@/contexts/ThemeContext';
import { ActivationBanner } from '@/components/activation/ActivationBanner';
import { CommandPalette } from '@/components/ui/command-palette';
import { cn } from '@/lib/utils';

const primaryNav = [
  { name: 'Workspace', href: '/dashboard/overview', icon: LayoutGrid },
  { name: 'Campaigns', href: '/dashboard/campaigns', icon: FolderKanban },
  { name: 'CDP', href: '/dashboard/cdp', icon: Users },
  { name: 'Assets', href: '/dashboard/assets', icon: Package },
  { name: 'Settings', href: '/dashboard/settings', icon: Settings },
];

const secondaryNav = [
  { name: 'Meta Setup', href: '/dashboard/activation', icon: Link2 },
];

function isNavActive(pathname: string, href: string) {
  if (href === '/dashboard/overview') {
    return pathname === '/dashboard/overview' || pathname === '/dashboard';
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function StudioAppShell() {
  const { user, logout } = useAuth();
  const { resolvedTheme, setTheme } = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
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

  const onSearchSubmit = (e: FormEvent) => {
    e.preventDefault();
    // CommandPalette listens for ⌘K; focus search is enough affordance.
  };

  const Sidebar = (
    <aside
      className="flex h-full flex-col border-r"
      style={{
        width: 'var(--studio-sidebar-width)',
        background: 'var(--studio-sidebar)',
        borderColor: 'var(--studio-border)',
      }}
    >
      <div className="flex items-center gap-3 px-5 py-5">
        <div
          className="flex h-9 w-9 items-center justify-center rounded-[10px] text-sm font-semibold"
          style={{
            background: 'var(--studio-accent)',
            color: 'var(--studio-accent-contrast)',
          }}
        >
          S
        </div>
        <div>
          <div className="text-sm font-semibold" style={{ color: 'var(--studio-text)' }}>
            Stratum AI
          </div>
          <div className="text-xs" style={{ color: 'var(--studio-text-secondary)' }}>
            Studio
          </div>
        </div>
      </div>

      <nav className="flex flex-1 flex-col gap-1 px-3">
        {primaryNav.map((item) => {
          const active = isNavActive(location.pathname, item.href);
          const Icon = item.icon;
          return (
            <NavLink
              key={item.href}
              to={item.href}
              onClick={() => setMobileOpen(false)}
              className={cn(
                'flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-sm font-medium transition-colors'
              )}
              style={{
                background: active ? 'var(--studio-nav-active)' : 'transparent',
                color: active ? 'var(--studio-accent)' : 'var(--studio-text-secondary)',
                boxShadow: active ? 'inset 3px 0 0 var(--studio-accent)' : undefined,
              }}
            >
              <Icon size={18} strokeWidth={1.75} />
              {item.name}
            </NavLink>
          );
        })}

        <div
          className="my-3 h-px"
          style={{ background: 'var(--studio-border)' }}
        />

        {secondaryNav.map((item) => {
          const active = isNavActive(location.pathname, item.href);
          const Icon = item.icon;
          return (
            <NavLink
              key={item.href}
              to={item.href}
              onClick={() => setMobileOpen(false)}
              className="flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-sm font-medium"
              style={{
                background: active ? 'var(--studio-nav-active)' : 'transparent',
                color: active ? 'var(--studio-accent)' : 'var(--studio-text-secondary)',
                boxShadow: active ? 'inset 3px 0 0 var(--studio-accent)' : undefined,
              }}
            >
              <Icon size={18} strokeWidth={1.75} />
              {item.name}
            </NavLink>
          );
        })}
      </nav>

      <div className="mt-auto space-y-3 border-t p-4" style={{ borderColor: 'var(--studio-border)' }}>
        <div className="flex items-center gap-3">
          <div
            className="flex h-9 w-9 items-center justify-center rounded-full text-xs font-semibold"
            style={{
              background: 'var(--studio-nav-active)',
              color: 'var(--studio-accent)',
            }}
          >
            {initials}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium" style={{ color: 'var(--studio-text)' }}>
              {user?.name || 'User'}
            </div>
            <div className="truncate text-xs" style={{ color: 'var(--studio-text-secondary)' }}>
              {user?.email || 'Workspace'}
            </div>
          </div>
          <button
            type="button"
            onClick={() => logout()}
            className="rounded-lg p-2"
            style={{ color: 'var(--studio-text-secondary)' }}
            aria-label="Sign out"
          >
            <LogOut size={16} />
          </button>
        </div>
        <div
          className="rounded-[10px] px-3 py-3 text-xs"
          style={{
            background: 'var(--studio-nav-active)',
            color: 'var(--studio-text-secondary)',
          }}
        >
          Trust-gated Meta automation
          <div
            className="mt-2 h-0.5 w-10 rounded-full"
            style={{ background: 'var(--studio-accent)' }}
          />
        </div>
      </div>
    </aside>
  );

  return (
    <div className="studio-shell flex h-screen overflow-hidden">
      {/* Desktop sidebar */}
      <div className="hidden lg:flex">{Sidebar}</div>

      {/* Mobile sidebar */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 flex lg:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-black/40"
            aria-label="Close menu"
            onClick={() => setMobileOpen(false)}
          />
          <div className="relative z-10 h-full shadow-xl">{Sidebar}</div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header
          className="flex items-center gap-3 border-b px-4 py-3 lg:px-6"
          style={{
            background: 'var(--studio-sidebar)',
            borderColor: 'var(--studio-border)',
          }}
        >
          <button
            type="button"
            className="rounded-lg p-2 lg:hidden"
            style={{ color: 'var(--studio-text)' }}
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
          >
            <Menu size={20} />
          </button>

          <form onSubmit={onSearchSubmit} className="mx-auto w-full max-w-xl flex-1">
            <label className="relative block">
              <Search
                size={16}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"
                style={{ color: 'var(--studio-text-secondary)' }}
              />
              <input
                className="studio-search w-full rounded-[10px] border py-2.5 pl-10 pr-16 text-sm"
                style={{
                  background: 'var(--studio-search-bg)',
                  borderColor: 'var(--studio-border)',
                  color: 'var(--studio-text)',
                }}
                placeholder="Search campaigns, segments, assets…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <kbd
                className="absolute right-3 top-1/2 hidden -translate-y-1/2 rounded border px-1.5 py-0.5 text-[10px] sm:inline"
                style={{
                  borderColor: 'var(--studio-border)',
                  color: 'var(--studio-text-secondary)',
                }}
              >
                ⌘ K
              </kbd>
            </label>
          </form>

          <div className="flex items-center gap-2">
            <button
              type="button"
              className="rounded-[10px] border p-2"
              style={{
                borderColor: 'var(--studio-border)',
                color: 'var(--studio-text-secondary)',
              }}
              onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
              aria-label="Toggle theme"
            >
              {resolvedTheme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <button
              type="button"
              className="rounded-[10px] border p-2"
              style={{
                borderColor: 'var(--studio-border)',
                color: 'var(--studio-text-secondary)',
              }}
              aria-label="Notifications"
            >
              <Bell size={18} />
            </button>
            <button
              type="button"
              className="studio-btn-primary hidden sm:inline-flex"
              onClick={() => navigate('/dashboard/campaigns?create=1')}
            >
              + New campaign
            </button>
          </div>
        </header>

        <ActivationBanner />

        <main
          className="flex-1 overflow-y-auto"
          style={{ background: 'var(--studio-bg)' }}
        >
          <Outlet />
        </main>
      </div>

      <CommandPalette />
    </div>
  );
}
