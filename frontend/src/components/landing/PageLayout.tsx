/**
 * PageLayout — public content pages (Studio tokens + MarketingShell).
 */

import { MarketingShell } from '@/components/marketing/MarketingShell';

interface PageLayoutProps {
  children: React.ReactNode;
}

export function PageLayout({ children }: PageLayoutProps) {
  return (
    <MarketingShell>
      <div className="mx-auto max-w-[960px] px-6 py-12" style={{ minHeight: '60vh' }}>
        {children}
      </div>
    </MarketingShell>
  );
}
