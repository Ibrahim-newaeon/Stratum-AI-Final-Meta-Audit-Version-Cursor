/**
 * Radix Themes provider — Stratum brand tokens (midnight + teal).
 */

import { Theme } from '@radix-ui/themes';
import '@radix-ui/themes/styles.css';
import type { ReactNode } from 'react';

interface StratumThemeProviderProps {
  children: ReactNode;
}

export function StratumThemeProvider({ children }: StratumThemeProviderProps) {
  return (
    <Theme
      appearance="dark"
      accentColor="teal"
      grayColor="slate"
      radius="medium"
      scaling="100%"
      panelBackground="translucent"
      style={{
        minHeight: '100%',
        background: 'var(--color-background)',
        color: 'var(--gray-12)',
      }}
    >
      {children}
    </Theme>
  );
}
