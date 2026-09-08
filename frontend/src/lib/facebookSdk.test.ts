/**
 * Tests for the Facebook JS SDK loader.
 *
 * The loader's job is narrow but load-bearing: inject the script at most once,
 * initialise `FB` exactly once, and never render a login path when the feature
 * is not configured. Each of those is asserted here; the SDK itself is stubbed,
 * so no request goes to Meta.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  credentialFrom,
  facebookLogin,
  getFacebookLoginStatus,
  loadFacebookSdk,
  resetFacebookSdkForTests,
} from './facebookSdk';
import type { FacebookSdkConfig } from './facebookSdk';

const CONFIG: FacebookSdkConfig = {
  enabled: true,
  app_id: '1510739363341814',
  api_version: 'v23.0',
  config_id: null,
  scopes: ['public_profile', 'email'],
};

/**
 * Stand in for the global the real script defines when it loads.
 *
 * The individual mocks are returned alongside the object so assertions read a
 * plain binding rather than an unbound method off `FB`.
 */
function installFakeFb() {
  const init = vi.fn();
  const getLoginStatus = vi.fn();
  const login = vi.fn();
  const logout = vi.fn();
  const fb = { init, getLoginStatus, login, logout };
  window.FB = fb as unknown as typeof window.FB;
  return { fb, init, getLoginStatus, login, logout };
}

/** Simulate the injected script finishing, which fires `fbAsyncInit`. */
function completeScriptLoad() {
  const fake = installFakeFb();
  window.fbAsyncInit?.();
  return fake;
}

beforeEach(() => {
  resetFacebookSdkForTests();
  delete window.FB;
  delete window.fbAsyncInit;
  document.getElementById('facebook-jssdk')?.remove();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('loadFacebookSdk', () => {
  it('rejects when the feature is disabled', async () => {
    await expect(loadFacebookSdk({ enabled: false })).rejects.toThrow(
      'Facebook Login is not configured'
    );
    // Nothing may reach Meta when the deployment has this switched off.
    expect(document.getElementById('facebook-jssdk')).toBeNull();
  });

  it('rejects when the app id is missing', async () => {
    await expect(
      loadFacebookSdk({ enabled: true, app_id: null, api_version: 'v23.0' })
    ).rejects.toThrow('Facebook Login is not configured');
    expect(document.getElementById('facebook-jssdk')).toBeNull();
  });

  it('injects the SDK script once and initialises with the served config', async () => {
    const promise = loadFacebookSdk(CONFIG);

    const script = document.getElementById('facebook-jssdk') as HTMLScriptElement;
    expect(script).not.toBeNull();
    expect(script.src).toBe('https://connect.facebook.net/en_US/sdk.js');
    expect(script.async).toBe(true);
    expect(script.crossOrigin).toBe('anonymous');

    const { init } = completeScriptLoad();
    await promise;

    expect(init).toHaveBeenCalledWith({
      appId: CONFIG.app_id,
      autoLogAppEvents: true,
      // This app renders its own button, so there is no XFBML to parse.
      xfbml: false,
      version: CONFIG.api_version,
    });
  });

  it('deduplicates concurrent callers into a single script and init', async () => {
    const first = loadFacebookSdk(CONFIG);
    const second = loadFacebookSdk(CONFIG);

    const { init } = completeScriptLoad();
    const [a, b] = await Promise.all([first, second]);

    expect(a).toBe(b);
    expect(document.querySelectorAll('#facebook-jssdk')).toHaveLength(1);
    expect(init).toHaveBeenCalledTimes(1);
  });

  it('refuses a second init with a different app id rather than pretending', async () => {
    const promise = loadFacebookSdk(CONFIG);
    completeScriptLoad();
    await promise;

    await expect(loadFacebookSdk({ ...CONFIG, app_id: '999' })).rejects.toThrow(
      'already initialised with another app id'
    );
  });

  it('allows a retry after the script fails to load', async () => {
    const promise = loadFacebookSdk(CONFIG);
    const script = document.getElementById('facebook-jssdk') as HTMLScriptElement;

    script.onerror?.(new Event('error'));
    await expect(promise).rejects.toThrow('Could not load the Facebook SDK');

    // An ad blocker or a flaky network must not disable the button for the
    // rest of the session, so the failed tag is removed and a retry re-injects.
    expect(document.getElementById('facebook-jssdk')).toBeNull();
    const retry = loadFacebookSdk(CONFIG);
    expect(document.getElementById('facebook-jssdk')).not.toBeNull();
    completeScriptLoad();
    await expect(retry).resolves.toBeDefined();
  });
});

describe('getFacebookLoginStatus', () => {
  it('resolves with whatever status the SDK reports', async () => {
    const { fb, getLoginStatus } = installFakeFb();
    getLoginStatus.mockImplementation((cb: (r: unknown) => void) =>
      cb({ status: 'connected', authResponse: { accessToken: 'tok' } })
    );

    const response = await getFacebookLoginStatus(fb as never);
    expect(response.status).toBe('connected');
    expect(response.authResponse?.accessToken).toBe('tok');
  });
});

describe('facebookLogin', () => {
  it('requests the configured scopes when there is no config_id', async () => {
    const { fb, login } = installFakeFb();
    login.mockImplementation((cb: (r: unknown) => void) => cb({ status: 'unknown' }));

    await facebookLogin(fb as never, CONFIG);

    expect(login).toHaveBeenCalledWith(expect.any(Function), {
      scope: 'public_profile,email',
      return_scopes: true,
    });
  });

  it('passes config_id for Facebook Login for Business', async () => {
    const { fb, login } = installFakeFb();
    login.mockImplementation((cb: (r: unknown) => void) => cb({ status: 'unknown' }));

    await facebookLogin(fb as never, { ...CONFIG, config_id: 'cfg-123' });

    // config_id replaces the scope list; sending both is not a valid call.
    // response_type must be 'code': Login for Business rejects the default
    // 'token' with "response_type=token is not supported in this flow".
    // override_default_response_type is not optional: without it the SDK
    // appends its own token,signed_request,graph_domain and Meta refuses the
    // dialog, which is exactly the bug this asserts against.
    expect(login).toHaveBeenCalledWith(expect.any(Function), {
      config_id: 'cfg-123',
      response_type: 'code',
      override_default_response_type: true,
    });
  });
});

describe('credentialFrom', () => {
  it('prefers the code the Login-for-Business flow returns', () => {
    expect(
      credentialFrom({ status: 'connected', authResponse: { code: 'c-1' } })
    ).toEqual({ code: 'c-1' });
  });

  it('falls back to the access token of the classic flow', () => {
    expect(
      credentialFrom({ status: 'connected', authResponse: { accessToken: 'tok' } })
    ).toEqual({ access_token: 'tok' });
  });

  it('sends the code when Meta returns both, so the secret stays server-side', () => {
    expect(
      credentialFrom({ status: 'connected', authResponse: { code: 'c-1', accessToken: 'tok' } })
    ).toEqual({ code: 'c-1' });
  });

  it.each([
    ['a dismissed dialog', { status: 'unknown' as const }],
    ['a declined app', { status: 'not_authorized' as const }],
    ['a connected status with no authResponse', { status: 'connected' as const }],
    ['a connected status with an empty authResponse', { status: 'connected' as const, authResponse: {} }],
  ])('returns null for %s', (_label, response) => {
    expect(credentialFrom(response)).toBeNull();
  });
});
