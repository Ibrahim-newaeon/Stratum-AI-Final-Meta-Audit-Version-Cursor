/**
 * Marketing site shell — Studio Pearl & Indigo / Navy & Periwinkle tokens.
 */

import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Menu, Moon, Sun, X } from 'lucide-react';
import { useTheme } from '@/contexts/ThemeContext';

const navLinks = [
  { name: 'Features', href: '/features' },
  { name: 'Pricing', href: '/pricing' },
  { name: 'Solutions', href: '/solutions/cdp' },
  { name: 'Resources', href: '/resources' },
  { name: 'Docs', href: '/docs' },
  { name: 'Company', href: '/about' },
];

interface MarketingShellProps {
  children: React.ReactNode;
}

export function MarketingShell({ children }: MarketingShellProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const { i18n } = useTranslation();
  const { resolvedTheme, setTheme } = useTheme();

  useEffect(() => {
    document.documentElement.dir = i18n.language === 'ar' ? 'rtl' : 'ltr';
    document.documentElement.lang = i18n.language || 'en';
  }, [i18n.language]);

  const isActive = (href: string) =>
    href === '/solutions/cdp'
      ? location.pathname.startsWith('/solutions')
      : location.pathname === href || location.pathname.startsWith(`${href}/`);

  return (
    <div className="studio-shell flex min-h-screen flex-col">
      <header
        className="sticky top-0 z-50 border-b"
        style={{
          background: 'color-mix(in srgb, var(--studio-sidebar) 92%, transparent)',
          backdropFilter: 'blur(12px)',
          borderColor: 'var(--studio-border)',
        }}
      >
        <div className="mx-auto flex max-w-[1200px] items-center justify-between gap-4 px-6 py-3">
          <Link to="/" className="flex items-center gap-3 no-underline">
            <div
              className="flex h-9 w-9 items-center justify-center rounded-[10px] text-sm font-semibold"
              style={{
                background: 'var(--studio-accent)',
                color: 'var(--studio-accent-contrast)',
              }}
            >
              S
            </div>
            <span className="text-sm font-semibold" style={{ color: 'var(--studio-text)' }}>
              Stratum AI
            </span>
          </Link>

          <nav className="hidden items-center gap-5 md:flex">
            {navLinks.map((link) => (
              <Link
                key={link.href}
                to={link.href}
                className="text-sm no-underline"
                style={{
                  color: isActive(link.href) ? 'var(--studio-accent)' : 'var(--studio-text-secondary)',
                }}
              >
                {link.name}
              </Link>
            ))}
          </nav>

          <div className="flex items-center gap-2">
            <button
              type="button"
              className="rounded-[10px] border p-2"
              style={{ borderColor: 'var(--studio-border)', color: 'var(--studio-text-secondary)' }}
              onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
              aria-label="Toggle theme"
            >
              {resolvedTheme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <Link to="/login" className="studio-btn-ghost hidden sm:inline-flex">
              Log in
            </Link>
            <Link to="/signup" className="studio-btn-primary">
              Start free
            </Link>
            <button
              type="button"
              className="rounded-[10px] border p-2 md:hidden"
              style={{ borderColor: 'var(--studio-border)' }}
              onClick={() => setMobileOpen((o) => !o)}
              aria-label="Toggle menu"
            >
              {mobileOpen ? <X size={18} /> : <Menu size={18} />}
            </button>
          </div>
        </div>
        {mobileOpen && (
          <div className="flex flex-col gap-2 border-t px-6 py-3 md:hidden" style={{ borderColor: 'var(--studio-border)' }}>
            {navLinks.map((link) => (
              <Link
                key={link.href}
                to={link.href}
                onClick={() => setMobileOpen(false)}
                className="py-2 text-sm no-underline"
                style={{ color: 'var(--studio-text)' }}
              >
                {link.name}
              </Link>
            ))}
          </div>
        )}
      </header>

      <main className="flex-1">{children}</main>

      <footer className="mt-20 border-t" style={{ borderColor: 'var(--studio-border)' }}>
        <div className="mx-auto flex max-w-[1200px] flex-col justify-between gap-4 px-6 py-8 sm:flex-row">
          <p className="text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
            © {new Date().getFullYear()} Stratum AI — Trust-Gated Autopilot for Meta
          </p>
          <div className="flex gap-4 text-sm">
            <Link to="/privacy" style={{ color: 'var(--studio-text-secondary)' }}>
              Privacy
            </Link>
            <Link to="/terms" style={{ color: 'var(--studio-text-secondary)' }}>
              Terms
            </Link>
            <Link to="/contact" style={{ color: 'var(--studio-text-secondary)' }}>
              Contact
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
