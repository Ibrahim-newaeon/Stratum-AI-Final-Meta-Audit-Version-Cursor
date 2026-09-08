/**
 * Stratum AI - Facebook JS SDK loader
 *
 * Loads `connect.facebook.net/en_US/sdk.js`, calls `FB.init` once, and exposes
 * the two calls the sign-in button needs: `getFacebookLoginStatus` and
 * `facebookLogin`.
 *
 * Why the SDK is loaded here and not from `index.html`
 * ---------------------------------------------------
 * The same rule the project already applies to Paddle.js: a third-party script
 * in `public/*.html` runs for every visitor of every page, including the
 * marketing site, whether or not the feature is switched on. This module
 * injects the tag on demand — the first time a Facebook button actually
 * renders — so a deployment with Facebook Login disabled ships no Meta script
 * at all, and no request reaches Meta until someone is about to use it.
 *
 * Configuration comes from `GET /api/v1/auth/facebook/config` rather than a
 * build-time `VITE_` variable, so the app id, Graph version and scopes have one
 * source of truth (the backend's settings) and a rebuild is not required to
 * change them.
 *
 * Two credential shapes, decided by the Meta app, not by this code
 * ----------------------------------------------------------------
 * A **Facebook Login for Business** app rejects the implicit flow outright
 * (`response_type=token is not supported in this flow`). It requires a
 * `config_id` and `response_type: 'code'`, and returns `authResponse.code`.
 * A **classic Facebook Login** app returns `authResponse.accessToken`.
 *
 * The backend accepts either and verifies both the same way, so the only thing
 * that decides which is sent is whether the served config carries a `config_id`.
 *
 * Nothing here is trusted as proof of identity. The credential is posted to the
 * backend, which re-derives the person server-side; the browser's `userID` is
 * never sent and never used.
 */

/** Shape of `FB.getLoginStatus` / `FB.login` responses (the fields used here). */
export interface FacebookAuthResponse {
  /** Present in the classic (implicit) flow only. */
  accessToken?: string;
  /** Present in the Facebook Login for Business code flow only. */
  code?: string;
  expiresIn?: number;
  signedRequest?: string;
  userID?: string;
  graphDomain?: string;
}

export type FacebookLoginStatus = 'connected' | 'not_authorized' | 'unknown';

export interface FacebookStatusResponse {
  status: FacebookLoginStatus;
  authResponse?: FacebookAuthResponse | null;
}

/** Public SDK configuration served by the backend. */
export interface FacebookSdkConfig {
  enabled: boolean;
  app_id?: string | null;
  api_version?: string | null;
  config_id?: string | null;
  use_code_flow?: boolean;
  scopes?: string[];
}

interface FacebookSdk {
  init(options: {
    appId: string;
    autoLogAppEvents?: boolean;
    xfbml?: boolean;
    version: string;
  }): void;
  getLoginStatus(callback: (response: FacebookStatusResponse) => void, force?: boolean): void;
  login(
    callback: (response: FacebookStatusResponse) => void,
    options?: {
      scope?: string;
      config_id?: string;
      auth_type?: string;
      return_scopes?: boolean;
      response_type?: 'code' | 'token';
      override_default_response_type?: boolean;
    }
  ): void;
  logout(callback: (response: unknown) => void): void;
}

declare global {
  interface Window {
    FB?: FacebookSdk;
    fbAsyncInit?: () => void;
  }
}

const SDK_SRC = 'https://connect.facebook.net/en_US/sdk.js';
const SDK_SCRIPT_ID = 'facebook-jssdk';

/**
 * In-flight/settled loader promise.
 *
 * Deduplicates concurrent callers: the login page and the settings page can
 * both ask at once, and `FB.init` must run exactly once per document.
 */
let sdkPromise: Promise<FacebookSdk> | null = null;

/** Config the SDK was initialised with, so a mismatched re-init is caught. */
let initialisedWith: FacebookSdkConfig | null = null;

/**
 * Load the Facebook JS SDK and initialise it against `config`.
 *
 * Resolves with the global `FB` object. Rejects when the config is unusable or
 * the script fails to load — the caller renders nothing rather than a button
 * that cannot work.
 */
export function loadFacebookSdk(config: FacebookSdkConfig): Promise<FacebookSdk> {
  if (!config.enabled || !config.app_id || !config.api_version) {
    return Promise.reject(new Error('Facebook Login is not configured'));
  }

  if (sdkPromise) {
    // A second call with a different app id would silently keep the first
    // init, so surface it instead of pretending the new config took effect.
    if (initialisedWith && initialisedWith.app_id !== config.app_id) {
      return Promise.reject(new Error('Facebook SDK already initialised with another app id'));
    }
    return sdkPromise;
  }

  initialisedWith = config;
  sdkPromise = new Promise<FacebookSdk>((resolve, reject) => {
    const appId = config.app_id as string;
    const version = config.api_version as string;

    const init = () => {
      if (!window.FB) {
        reject(new Error('Facebook SDK loaded without exposing FB'));
        return;
      }
      window.FB.init({
        appId,
        autoLogAppEvents: true,
        // No XFBML: this app renders its own button rather than <fb:login-button>,
        // so there is no markup for the SDK to parse and nothing to gain from
        // letting it walk the DOM of a React tree it does not control.
        xfbml: false,
        version,
      });
      resolve(window.FB);
    };

    // Already present (a previous mount, or a hot reload): initialise directly.
    if (window.FB) {
      init();
      return;
    }

    window.fbAsyncInit = init;

    const existing = document.getElementById(SDK_SCRIPT_ID);
    if (existing) {
      // The tag is there but has not fired yet; fbAsyncInit above will run.
      return;
    }

    const script = document.createElement('script');
    script.id = SDK_SCRIPT_ID;
    script.src = SDK_SRC;
    script.async = true;
    script.defer = true;
    script.crossOrigin = 'anonymous';
    script.onerror = () => {
      // Let a later attempt retry from scratch: an ad blocker or a flaky
      // network should not disable the button for the rest of the session.
      sdkPromise = null;
      initialisedWith = null;
      script.remove();
      reject(new Error('Could not load the Facebook SDK'));
    };
    document.head.appendChild(script);
  });

  return sdkPromise;
}

/**
 * Read the current login status, exactly as Meta's "Check Login Status" step
 * describes: `connected`, `not_authorized` or `unknown`.
 *
 * Used to decide whether the button can reuse an existing Facebook session
 * instead of opening the login dialog. It is never treated as proof that the
 * person is signed in to Stratum — only the backend decides that.
 */
export function getFacebookLoginStatus(fb: FacebookSdk): Promise<FacebookStatusResponse> {
  return new Promise((resolve) => {
    fb.getLoginStatus((response) => resolve(response));
  });
}

/**
 * Open the Facebook login dialog and resolve with the resulting status.
 *
 * Meta documents two Login-for-Business recipes, and which one applies is a
 * property of the saved configuration rather than a choice this code makes:
 *
 * - A **User access token** configuration takes `config_id` and nothing else.
 *   Its example is literally `{ config_id: '<CONFIG_ID>' }`; the dialog returns
 *   an access token. This is the shape a website sign-in button wants, because
 *   it is the only one that identifies a *person*.
 * - A **System User** configuration "require[s] the authorization code grant
 *   type", so `response_type` is `'code'` and
 *   `override_default_response_type` "must be set to true. When true, any
 *   response types passed in the response_type will take precedence over the
 *   default types". Without that second flag the SDK keeps appending its own
 *   `token,signed_request,graph_domain` and the dialog fails before it renders
 *   with `response_type=token is not supported in this flow`.
 *
 * `use_code_flow` from the served config picks between them. Sending `code` to
 * a User access token configuration, or `token` to a System User one, fails the
 * dialog either way - hence a setting rather than a guess.
 *
 * Without a `config_id` the classic scope list is used, and `return_scopes`
 * asks Meta to report which permissions were actually granted, since the person
 * may decline `email`.
 */
export function facebookLogin(
  fb: FacebookSdk,
  config: FacebookSdkConfig
): Promise<FacebookStatusResponse> {
  let options: Parameters<FacebookSdk['login']>[1];
  if (config.config_id && config.use_code_flow) {
    options = {
      config_id: config.config_id,
      response_type: 'code',
      override_default_response_type: true,
    };
  } else if (config.config_id) {
    options = { config_id: config.config_id };
  } else {
    options = {
      scope: (config.scopes ?? ['public_profile', 'email']).join(','),
      return_scopes: true,
    };
  }

  return new Promise((resolve) => {
    fb.login((response) => resolve(response), options);
  });
}

/** What the backend accepts: exactly one of the two credential shapes. */
export type FacebookCredential = { code: string } | { access_token: string };

/**
 * Pull the credential out of an SDK response, or null when there is none.
 *
 * A dialog the person dismissed, a `not_authorized` status and a `connected`
 * status with an empty `authResponse` all land on null, and the caller treats
 * all three as "not completed" rather than as an error.
 */
export function credentialFrom(response: FacebookStatusResponse): FacebookCredential | null {
  if (response.status !== 'connected' || !response.authResponse) {
    return null;
  }
  const { code, accessToken } = response.authResponse;
  if (code) return { code };
  if (accessToken) return { access_token: accessToken };
  return null;
}

/**
 * Reset module state. Test-only: production code loads the SDK once per page.
 */
export function resetFacebookSdkForTests(): void {
  sdkPromise = null;
  initialisedWith = null;
}
