/**
 * Design system content page — mirrors the concept board article layout.
 */

import { Link } from 'react-router-dom';
import { ChevronRight, Pencil } from 'lucide-react';

const toc = [
  'Built for clarity',
  'Color',
  'Typography',
  'Spacing',
  'Components',
];

const related = [
  { name: 'Meta Setup', href: '/dashboard/activation' },
  { name: 'Asset library', href: '/dashboard/assets' },
  { name: 'Campaigns', href: '/dashboard/campaigns' },
  { name: 'Settings', href: '/dashboard/settings' },
];

const swatches = [
  { light: '#4F46E5', dark: '#A5B4FC', label: 'Accent' },
  { light: '#6366F1', dark: '#818CF8', label: 'Support' },
  { light: '#A5B4FC', dark: '#33415F', label: 'Wash' },
  { light: '#E0E7FF', dark: '#0F1424', label: 'Surface' },
];

export default function DesignSystemPage() {
  return (
    <div className="mx-auto max-w-[1200px] px-6 py-8">
      <div className="mb-4 text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
        Content / <span style={{ color: 'var(--studio-text)' }}>Design system</span>
      </div>

      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-3xl font-semibold" style={{ color: 'var(--studio-text)' }}>
              Design system
            </h1>
            <span
              className="rounded-full px-2.5 py-1 text-xs font-medium"
              style={{
                background: 'var(--studio-success-wash)',
                color: 'var(--studio-success)',
              }}
            >
              Published
            </span>
          </div>
          <p className="mt-2 max-w-xl text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
            Principles, patterns, and components for consistent Stratum Studio experiences —
            Pearl & Indigo (light) and Navy & Periwinkle (dark).
          </p>
        </div>
        <button type="button" className="studio-btn-ghost">
          <Pencil size={14} />
          Edit page
        </button>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_260px]">
        <article className="studio-card overflow-hidden">
          <div className="studio-art relative flex h-48 items-end p-6 md:h-56">
            <p className="max-w-md text-lg font-medium text-white drop-shadow">
              A more considered creative process.
            </p>
          </div>
          <div className="space-y-5 px-6 py-8 md:px-10" style={{ maxWidth: 720 }}>
            <h2 className="text-2xl font-semibold" style={{ color: 'var(--studio-text)' }}>
              Built for clarity
            </h2>
            <p className="text-[15px] leading-7" style={{ color: 'var(--studio-text-secondary)' }}>
              Stratum Studio uses the same geometry in light and dark: narrow sidebar, centered
              search, project cards, and a reading column with restrained width. Only color tokens
              and artwork change between Pearl & Indigo and Navy & Periwinkle.
            </p>
            <p className="text-[15px] leading-7" style={{ color: 'var(--studio-text-secondary)' }}>
              Gradients stay in artwork. Surfaces stay solid. Focus rings and active washes make
              selection obvious without neon glow or heavy shadows.
            </p>

            <div className="grid grid-cols-1 gap-4 pt-2 sm:grid-cols-2">
              <div className="studio-card p-4">
                <h3 className="text-sm font-semibold" style={{ color: 'var(--studio-text)' }}>
                  Color
                </h3>
                <div className="mt-4 flex flex-wrap gap-3">
                  {swatches.map((s) => (
                    <div key={s.label} className="text-center">
                      <div
                        className="mx-auto h-10 w-10 rounded-full border dark:hidden"
                        style={{ background: s.light, borderColor: 'var(--studio-border)' }}
                      />
                      <div
                        className="mx-auto hidden h-10 w-10 rounded-full border dark:block"
                        style={{ background: s.dark, borderColor: 'var(--studio-border)' }}
                      />
                      <div className="mt-1 text-[10px]" style={{ color: 'var(--studio-text-secondary)' }}>
                        {s.label}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="studio-card p-4">
                <h3 className="text-sm font-semibold" style={{ color: 'var(--studio-text)' }}>
                  Typography
                </h3>
                <div className="mt-3 flex items-end gap-4">
                  <span className="text-4xl font-semibold" style={{ color: 'var(--studio-text)' }}>
                    Aa
                  </span>
                  <div className="text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
                    <div className="font-medium" style={{ color: 'var(--studio-text)' }}>
                      Inter
                    </div>
                    <div>Light, Regular, Medium, Semibold</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </article>

        <aside className="space-y-4">
          <div className="studio-card p-4">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--studio-text-secondary)' }}>
              On this page
            </h3>
            <ul className="space-y-1">
              {toc.map((item, i) => (
                <li key={item}>
                  <span
                    className="block rounded-r-[8px] py-1.5 pl-3 text-sm"
                    style={{
                      borderLeft: `3px solid ${i === 0 ? 'var(--studio-accent)' : 'transparent'}`,
                      color: i === 0 ? 'var(--studio-text)' : 'var(--studio-text-secondary)',
                      background: i === 0 ? 'var(--studio-nav-active)' : 'transparent',
                    }}
                  >
                    {item}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          <div className="studio-card p-4">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--studio-text-secondary)' }}>
              Related pages
            </h3>
            <ul className="space-y-1">
              {related.map((item) => (
                <li key={item.href}>
                  <Link
                    to={item.href}
                    className="flex items-center justify-between rounded-[8px] px-2 py-2 text-sm hover:opacity-90"
                    style={{ color: 'var(--studio-text)' }}
                  >
                    {item.name}
                    <ChevronRight size={14} style={{ color: 'var(--studio-text-secondary)' }} />
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </aside>
      </div>
    </div>
  );
}
