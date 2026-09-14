/**
 * Design system page — Evidence Room tokens reference
 */

import { Link } from 'react-router-dom';

const SWATCHES = [
  { name: 'Bone', varName: '--bone' },
  { name: 'Paper', varName: '--paper' },
  { name: 'Carbon', varName: '--carbon' },
  { name: 'Oxblood', varName: '--oxblood' },
  { name: 'Deep teal', varName: '--deep-teal' },
  { name: 'Signal orange', varName: '--signal-orange' },
];

export default function DesignSystemPage() {
  return (
    <div className="mx-auto max-w-[960px] px-6 py-8">
      <p className="text-sm" style={{ color: 'var(--er-muted)' }}>
        Content / <span style={{ color: 'var(--er-text)' }}>Design system</span>
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <h1 className="er-serif text-4xl">Evidence Room</h1>
        <span className="er-status er-status-pass">
          <span className="er-status-dot" /> Published
        </span>
      </div>
      <p className="mt-3 max-w-xl text-sm" style={{ color: 'var(--er-muted)' }}>
        Paper, ink, intervention — Instrument Serif, IBM Plex Sans, IBM Plex Mono. Restraint is the
        spectacle.
      </p>

      <div className="er-seam mt-8" />

      <div className="er-panel mt-8 overflow-hidden">
        <div className="flex h-40 items-end p-6" style={{ background: 'var(--er-surface-2)' }}>
          <p className="er-serif text-2xl">Every decision leaves a paper trail.</p>
        </div>
        <div className="er-seam-open" />
        <div className="space-y-4 px-6 py-8">
          <h2 className="er-serif text-2xl">Built for scrutiny</h2>
          <p className="text-sm leading-7" style={{ color: 'var(--er-muted)' }}>
            Status always includes text. Seams separate recommendation from evidence. Sample data is
            labeled. No neon, no neural nets, no fabricated confidence scores.
          </p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {SWATCHES.map((s) => (
              <div key={s.name} className="er-tray p-3">
                <div className="h-10 w-full border" style={{ background: `var(${s.varName})`, borderColor: 'var(--er-border)' }} />
                <div className="er-mono mt-2 text-[11px]">{s.name}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <p className="mt-8 text-sm space-x-4">
        <Link to="/studio-preview/workspace" style={{ color: 'var(--er-accent)' }}>
          Evidence overview →
        </Link>
        <Link to="/studio-preview/luminous" style={{ color: 'var(--er-accent)' }}>
          Luminous Control (proposed) →
        </Link>
      </p>
    </div>
  );
}
