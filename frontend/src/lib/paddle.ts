/**
 * Stratum AI - Paddle.js v2 loader (Paddle Billing)
 *
 * Thin, typed wrapper around Paddle.js v2 for the overlay checkout used by
 * Settings > Billing. Paddle.js is loaded at runtime from the Paddle CDN
 * (https://cdn.paddle.com/paddle/v2/paddle.js) - there is no npm package and
 * no script tag in index.html - and only inside the authenticated SPA.
 *
 * Rules:
 * - The client-side token always comes from GET /api/v1/billing/config; it is
 *   never read from a Vite env variable.
 * - `Paddle.Environment.set('sandbox')` runs ONLY for the sandbox environment
 *   and always BEFORE `Paddle.Initialize`.
 * - Initialisation is memoised per token; the event callback registered with
 *   Paddle is a stable dispatcher so React components can swap listeners
 *   without re-initialising Paddle.js.
 */

import type { CheckoutSession, PaddleEnvironment } from '@/api/billing';

// =============================================================================
// Types
// =============================================================================

/** Paddle.js event as delivered to `eventCallback` (e.g. `checkout.completed`). */
export interface PaddleEvent {
  name: string;
  data?: unknown;
}

export type PaddleEventListener = (event: PaddleEvent) => void;

export interface PaddleCheckoutItem {
  priceId: string;
  quantity: number;
}

export interface PaddleCheckoutSettings {
  displayMode?: 'overlay' | 'inline';
  successUrl?: string;
  locale?: string;
  theme?: 'light' | 'dark';
  frameTarget?: string;
  frameInitialHeight?: number;
  frameStyle?: string;
}

export interface PaddleCheckoutOpenOptions {
  items: PaddleCheckoutItem[];
  customer?: { email?: string; id?: string };
  customData?: Record<string, unknown>;
  settings?: PaddleCheckoutSettings;
}

export interface PaddleInitializeOptions {
  token: string;
  eventCallback?: PaddleEventListener;
}

/** Minimal surface of the global `window.Paddle` object (Paddle.js v2). */
export interface PaddleJs {
  Environment: { set: (environment: PaddleEnvironment) => void };
  Initialize: (options: PaddleInitializeOptions) => void;
  Update?: (options: Partial<PaddleInitializeOptions>) => void;
  Checkout: {
    open: (options: PaddleCheckoutOpenOptions) => void;
    close: () => void;
  };
  Initialized?: boolean;
}

declare global {
  interface Window {
    Paddle?: PaddleJs;
  }
}

export interface InitializePaddleOptions {
  token: string;
  environment: PaddleEnvironment;
  eventCallback?: PaddleEventListener;
}

export interface OpenPaddleCheckoutOptions {
  locale?: string;
}

// =============================================================================
// Constants & module state
// =============================================================================

export const PADDLE_JS_URL = 'https://cdn.paddle.com/paddle/v2/paddle.js';
const SCRIPT_ATTRIBUTE = 'data-paddle-js';

let scriptPromise: Promise<void> | null = null;
let initializedToken: string | null = null;
let initializedEnvironment: PaddleEnvironment | null = null;
let currentCallback: PaddleEventListener | null = null;
const listeners = new Set<PaddleEventListener>();

/** Stable callback handed to Paddle.js once; fans events out to listeners. */
function dispatchPaddleEvent(event: PaddleEvent): void {
  if (currentCallback) {
    try {
      currentCallback(event);
    } catch (err) {
      console.error('[paddle] eventCallback failed', err);
    }
  }
  listeners.forEach((listener) => {
    try {
      listener(event);
    } catch (err) {
      console.error('[paddle] event listener failed', err);
    }
  });
}

// =============================================================================
// Public API
// =============================================================================

/**
 * Resolve the Paddle environment: an optional VITE_PADDLE_ENVIRONMENT override
 * wins, otherwise the environment reported by GET /billing/config is used.
 */
export function resolveEnvironment(apiEnv: PaddleEnvironment): PaddleEnvironment {
  const override = import.meta.env.VITE_PADDLE_ENVIRONMENT;
  if (override === 'sandbox' || override === 'production') {
    return override;
  }
  return apiEnv;
}

/**
 * Inject the Paddle.js v2 script tag once (idempotent). Reuses an existing
 * tag and resolves immediately when `window.Paddle` is already present.
 */
export function loadPaddleScript(): Promise<void> {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return Promise.reject(new Error('Paddle.js can only be loaded in a browser'));
  }
  if (window.Paddle) {
    return Promise.resolve();
  }
  if (scriptPromise) {
    return scriptPromise;
  }

  scriptPromise = new Promise<void>((resolve, reject) => {
    const existing =
      document.querySelector<HTMLScriptElement>(`script[${SCRIPT_ATTRIBUTE}]`) ??
      document.querySelector<HTMLScriptElement>(`script[src="${PADDLE_JS_URL}"]`);

    const handleLoad = (): void => {
      if (window.Paddle) {
        resolve();
      } else {
        scriptPromise = null;
        reject(new Error('Paddle.js loaded but window.Paddle is missing'));
      }
    };
    const handleError = (tag: HTMLScriptElement) => (): void => {
      scriptPromise = null;
      tag.remove();
      reject(new Error('Failed to load Paddle.js'));
    };

    if (existing) {
      existing.addEventListener('load', handleLoad, { once: true });
      existing.addEventListener('error', handleError(existing), { once: true });
      return;
    }

    const script = document.createElement('script');
    script.src = PADDLE_JS_URL;
    script.async = true;
    script.setAttribute(SCRIPT_ATTRIBUTE, 'true');
    script.addEventListener('load', handleLoad, { once: true });
    script.addEventListener('error', handleError(script), { once: true });
    document.head.appendChild(script);
  });

  return scriptPromise;
}

/**
 * Load Paddle.js and initialise it with the client-side token from the API.
 *
 * `Paddle.Environment.set('sandbox')` is called only when
 * `environment === 'sandbox'` and always before `Paddle.Initialize`.
 * Initialisation is memoised per token: calling again with the same token only
 * swaps the event callback; a different token is applied via `Paddle.Update`
 * when Paddle.js exposes it.
 */
export async function initializePaddle(options: InitializePaddleOptions): Promise<void> {
  const { token, environment, eventCallback } = options;
  if (!token) {
    throw new Error('A Paddle client-side token is required');
  }

  await loadPaddleScript();
  const paddle = window.Paddle;
  if (!paddle) {
    throw new Error('Paddle.js is not available');
  }

  currentCallback = eventCallback ?? null;

  if (initializedToken === token) {
    return;
  }

  if (initializedToken === null) {
    if (environment === 'sandbox') {
      paddle.Environment.set('sandbox');
    }
    paddle.Initialize({ token, eventCallback: dispatchPaddleEvent });
  } else if (typeof paddle.Update === 'function') {
    // Paddle.js can only be initialised once per page; switch tokens via Update.
    paddle.Update({ token, eventCallback: dispatchPaddleEvent });
  } else {
    throw new Error('Paddle.js is already initialised with a different token');
  }

  initializedToken = token;
  initializedEnvironment = environment;
}

/**
 * Open the Paddle overlay checkout for a session prepared by
 * POST /billing/checkout-session. `initializePaddle` must have run first.
 */
export async function openPaddleCheckout(
  session: CheckoutSession,
  options: OpenPaddleCheckoutOptions = {}
): Promise<void> {
  await loadPaddleScript();
  const paddle = window.Paddle;
  if (!paddle) {
    throw new Error('Paddle.js is not available');
  }
  if (initializedToken === null) {
    throw new Error('Paddle.js is not initialised - call initializePaddle first');
  }

  const settings: PaddleCheckoutSettings = {
    displayMode: 'overlay',
    successUrl: session.success_url,
  };
  if (options.locale) {
    settings.locale = options.locale;
  }

  paddle.Checkout.open({
    items: [{ priceId: session.price_id, quantity: 1 }],
    customer: { email: session.customer_email },
    customData: session.custom_data,
    settings,
  });
}

/** Close the overlay checkout if one is open (no-op otherwise). */
export function closePaddleCheckout(): void {
  try {
    window.Paddle?.Checkout.close();
  } catch {
    // Paddle throws when no checkout is open; ignore.
  }
}

/**
 * Subscribe to Paddle.js events independently of `initializePaddle`'s
 * `eventCallback`. Returns an unsubscribe function.
 */
export function subscribePaddleEvents(listener: PaddleEventListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** True once `initializePaddle` has completed for any token. */
export function isPaddleInitialized(): boolean {
  return initializedToken !== null;
}

/** Environment Paddle.js was initialised with (null before initialisation). */
export function getPaddleEnvironment(): PaddleEnvironment | null {
  return initializedEnvironment;
}
