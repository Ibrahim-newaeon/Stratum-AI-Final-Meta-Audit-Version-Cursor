/**
 * Portal launch surface: modules that still ship mock or unfinished UX.
 *
 * Finished modules must be removed from this list when they are wired to
 * real APIs. Keep `/test-page` hidden — it is a debug remnant.
 */

export const PORTAL_LAUNCH_HIDDEN_HREFS = [
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

/** This portal does not take payments in this portal release. */
export const PORTAL_PAYMENTS_ENABLED = false;
