/**
 * Portal launch surface: modules that still ship mock or unfinished UX.
 *
 * They stay in the repo so they can be finished later, but production
 * navigation, the command palette, and the dashboard routes must not
 * present them as live product.
 */

export const PORTAL_LAUNCH_HIDDEN_HREFS = [
  '/dashboard/custom-autopilot-rules',
  '/dashboard/custom-reports',
  '/dashboard/cdp/consent',
  '/dashboard/cdp/predictive-churn',
  '/dashboard/knowledge-graph/journeys',
  '/test-page',
] as const;

export type PortalLaunchHiddenHref = (typeof PORTAL_LAUNCH_HIDDEN_HREFS)[number];

export function isHiddenInPortalLaunch(pathname: string): boolean {
  const path = pathname.split('?')[0].replace(/\/+$/, '') || '/';
  return PORTAL_LAUNCH_HIDDEN_HREFS.some(
    (hidden) => path === hidden || path.endsWith(hidden)
  );
}

/** Demo-login buttons and credentials compile out of production Vite builds. */
export function isDemoLoginEnabled(): boolean {
  return import.meta.env.DEV;
}

/** This portal does not load a payment overlay or take cards. */
export const PORTAL_PAYMENTS_ENABLED = false;
