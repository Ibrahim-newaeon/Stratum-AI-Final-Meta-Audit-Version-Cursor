import { describe, expect, it } from 'vitest';
import {
  isDemoLoginEnabled,
  isHiddenInPortalLaunch,
  PORTAL_LAUNCH_HIDDEN_HREFS,
  PORTAL_PAYMENTS_ENABLED,
} from './portalLaunch';

describe('portal launch surface', () => {
  it('hides unfinished and mock-backed dashboard modules', () => {
    expect(PORTAL_LAUNCH_HIDDEN_HREFS).toEqual(
      expect.arrayContaining([
        '/dashboard/custom-autopilot-rules',
        '/dashboard/custom-reports',
        '/dashboard/cdp/consent',
        '/dashboard/cdp/predictive-churn',
        '/dashboard/knowledge-graph/insights',
        '/dashboard/knowledge-graph/journeys',
        '/test-page',
      ])
    );
  });

  it('matches tenant-prefixed and query-stripped paths', () => {
    expect(isHiddenInPortalLaunch('/dashboard/cdp/predictive-churn')).toBe(true);
    expect(isHiddenInPortalLaunch('/dashboard/cdp/predictive-churn?tab=risk')).toBe(true);
    expect(isHiddenInPortalLaunch('/t/acme/dashboard/custom-reports')).toBe(true);
    expect(isHiddenInPortalLaunch('/dashboard/campaigns')).toBe(false);
  });

  it('enables demo login only in Vite DEV (stripped from production bundles)', () => {
    expect(isDemoLoginEnabled()).toBe(import.meta.env.DEV);
  });

  it('does not take payments in this portal release', () => {
    expect(PORTAL_PAYMENTS_ENABLED).toBe(false);
  });
});
