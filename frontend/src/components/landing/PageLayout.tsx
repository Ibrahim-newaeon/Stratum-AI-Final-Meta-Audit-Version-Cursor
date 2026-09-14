/**
 * PageLayout — public content pages shell (Radix Themes + MarketingShell).
 */

import { Container } from '@radix-ui/themes';
import { MarketingShell } from '@/components/marketing/MarketingShell';
import { StratumThemeProvider } from '@/theme/StratumThemeProvider';

interface PageLayoutProps {
  children: React.ReactNode;
}

export function PageLayout({ children }: PageLayoutProps) {
  return (
    <StratumThemeProvider>
      <MarketingShell>
        <Container size="3" py="8" style={{ minHeight: '60vh' }}>
          {children}
        </Container>
      </MarketingShell>
    </StratumThemeProvider>
  );
}
