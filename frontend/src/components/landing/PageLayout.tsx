/**
 * Public content pages — Evidence Room shell
 */

import { EvidenceMarketingShell } from '@/components/evidence/EvidenceMarketingShell';

export function PageLayout({ children }: { children: React.ReactNode }) {
  return (
    <EvidenceMarketingShell>
      <div className="mx-auto max-w-[800px] px-6 py-12" style={{ minHeight: '60vh' }}>
        <article className="prose-evidence text-base leading-7 md:text-lg" style={{ color: 'var(--er-text)' }}>
          {children}
        </article>
      </div>
    </EvidenceMarketingShell>
  );
}
