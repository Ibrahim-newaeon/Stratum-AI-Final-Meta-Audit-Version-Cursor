import { describe, expect, it } from 'vitest';
import {
  isDemoLoginEnabled,
  isHiddenInPortalLaunch,
  PORTAL_LAUNCH_HIDDEN_HREFS,
  PORTAL_PAYMENTS_ENABLED,
} from './portalLaunch';

describe('portal launch surface', () => {
  it('only hides debug remnants after unfinished modules are wired', () => {
    expect(PORTAL_LAUNCH_HIDDEN_HREFS).toEqual(['/test-page']);
  });

  it('matches tenant-prefixed and query-stripped paths', () => {
    expect(isHiddenInPortalLaunch('/test-page')).toBe(true);
    expect(isHiddenInPortalLaunch('/test-page?x=1')).toBe(true);
    expect(isHiddenInPortalLaunch('/t/acme/test-page')).toBe(true);
    expect(isHiddenInPortalLaunch('/dashboard/campaigns')).toBe(false);
    expect(isHiddenInPortalLaunch('/dashboard/custom-reports')).toBe(false);
    expect(isHiddenInPortalLaunch('/dashboard/cdp/consent')).toBe(false);
    expect(isHiddenInPortalLaunch('/dashboard/cdp/predictive-churn')).toBe(false);
    expect(isHiddenInPortalLaunch('/dashboard/cdp/predictive-churn?tab=risk')).toBe(false);
    expect(isHiddenInPortalLaunch('/dashboard/knowledge-graph/insights')).toBe(false);
    expect(isHiddenInPortalLaunch('/dashboard/knowledge-graph/journeys')).toBe(false);
    expect(isHiddenInPortalLaunch('/dashboard/custom-autopilot-rules')).toBe(false);
  });

  it('enables demo login only in Vite DEV (stripped from production bundles)', () => {
    expect(isDemoLoginEnabled()).toBe(import.meta.env.DEV);
  });

  it('does not take payments in this portal release', () => {
    expect(PORTAL_PAYMENTS_ENABLED).toBe(false);
  });
});
