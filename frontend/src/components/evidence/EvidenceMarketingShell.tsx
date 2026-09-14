/**
 * Marketing shell — Evidence Room
 */

import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Menu, Moon, Sun, X } from 'lucide-react';
import { useTheme } from '@/contexts/ThemeContext';

const nav = [
  { name: 'How it works', href: '/#cross-examination' },
  { name: 'Evidence', href: '/#beneath' },
  { name: 'Integrations', href: '/#fit' },
  { name: 'Pricing', href: '/pricing' },
];

export function EvidenceMarketingShell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const { resolvedTheme, setTheme } = useTheme();
  const location = useLocation();

  useEffect(() => {
    document.documentElement.lang = 'en';
  }, []);

  return (
    <div className="er-shell">
      <header
        className="sticky top-0 z-40 border-b"
        style={{ background: 'color-mix(in srgb, var(--er-surface) 92%, transparent)', borderColor: 'var(--er-border)', backdropFilter: 'blur(8px)' }}
      >
        <div className="mx-auto flex max-w-[1200px] items-center justify-between gap-4 px-6 py-3">
          <Link to="/" className="flex items-center gap-3 no-underline">
            <div
              className="flex h-8 w-8 items-center justify-center text-xs font-semibold"
              style={{ background: 'var(--er-accent)', color: 'var(--er-accent-contrast)' }}
            >
              S
            </div>
            <span className="text-sm font-semibold" style={{ color: 'var(--er-text)' }}>
              StratumAI
            </span>
          </Link>

          <nav className="hidden items-center gap-6 md:flex" aria-label="Marketing">
            {nav.map((l) => (
              <a
                key={l.href}
                href={l.href}
                className="text-sm no-underline"
                style={{
                  color:
                    location.pathname === l.href ? 'var(--er-accent)' : 'var(--er-muted)',
                }}
              >
                {l.name}
              </a>
            ))}
          </nav>

          <div className="flex items-center gap-2">
            <button
              type="button"
              className="border p-2"
              style={{ borderColor: 'var(--er-border)', borderRadius: 'var(--er-radius)', color: 'var(--er-muted)' }}
              onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
              aria-label="Toggle theme"
            >
              {resolvedTheme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <Link to="/login" className="er-btn-ghost hidden sm:inline-flex !py-2 !px-3 text-sm">
              Sign in
            </Link>
            <Link to="/signup" className="er-btn-primary !py-2 !px-3 text-sm">
              Start free trial
            </Link>
            <button type="button" className="md:hidden" onClick={() => setOpen((o) => !o)} aria-label="Menu">
              {open ? <X size={18} /> : <Menu size={18} />}
            </button>
          </div>
        </div>
        {open && (
          <div className="flex flex-col gap-2 border-t px-6 py-3 md:hidden" style={{ borderColor: 'var(--er-border)' }}>
            {nav.map((l) => (
              <a key={l.href} href={l.href} onClick={() => setOpen(false)} className="py-2 text-sm" style={{ color: 'var(--er-text)' }}>
                {l.name}
              </a>
            ))}
          </div>
        )}
      </header>
      <main>{children}</main>
      <footer className="mt-24 border-t" style={{ borderColor: 'var(--er-border)' }}>
        <div className="mx-auto flex max-w-[1200px] flex-col justify-between gap-4 px-6 py-10 sm:flex-row">
          <p className="text-sm" style={{ color: 'var(--er-muted)' }}>
            © {new Date().getFullYear()} StratumAI — Every decision leaves a paper trail.
          </p>
          <div className="flex flex-wrap gap-4 text-sm">
            <Link to="/privacy" style={{ color: 'var(--er-muted)' }}>Privacy</Link>
            <Link to="/terms" style={{ color: 'var(--er-muted)' }}>Terms</Link>
            <Link to="/security" style={{ color: 'var(--er-muted)' }}>Security</Link>
            <Link to="/docs" style={{ color: 'var(--er-muted)' }}>Docs</Link>
            <Link to="/contact" style={{ color: 'var(--er-muted)' }}>Contact</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
