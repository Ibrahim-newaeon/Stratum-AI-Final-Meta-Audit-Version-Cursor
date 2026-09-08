/**
 * Stratum AI - "Continue with Facebook" button
 *
 * Renders nothing at all unless `GET /auth/facebook/config` reports the feature
 * enabled, so a deployment without Meta credentials shows no dead button and
 * loads no Meta script.
 *
 * The click path follows Meta's own sequence: check `FB.getLoginStatus` first
 * and reuse an existing `connected` session, otherwise open the login dialog
 * with `FB.login`. Either way the resulting `authResponse.accessToken` is
 * handed to `onToken`, which posts it to the backend for verification. Nothing
 * on this side decides who the person is.
 *
 * Accessibility: a real `<button>`, keyboard reachable, at least 44px tall,
 * `aria-busy` while a sign-in is in flight, and errors announced through a
 * `role="alert"` region rather than colour alone.
 */

import { useCallback, useState } from 'react';
import { useFacebookLoginConfig } from '@/api/facebookAuth';
import { facebookLogin, getFacebookLoginStatus, loadFacebookSdk } from '@/lib/facebookSdk';

interface FacebookLoginButtonProps {
  /** Receives the verified-by-Meta access token to exchange with the backend. */
  onToken: (accessToken: string) => Promise<void> | void;
  /** Surfaced when the SDK or the dialog fails, so the page can show one error. */
  onError?: (message: string) => void;
  /** Disables the button while the page is busy with another sign-in. */
  disabled?: boolean;
  label?: string;
  /** Theme tokens from the calling page, so the button matches its surface. */
  theme?: {
    textPrimary: string;
    textMuted: string;
    border: string;
    bgCard: string;
  };
}

const DEFAULT_THEME = {
  textPrimary: '#FFFFFF',
  textMuted: 'rgba(255, 255, 255, 0.5)',
  border: 'rgba(255, 255, 255, 0.10)',
  bgCard: 'rgba(18, 18, 26, 0.9)',
};

/** Meta's brand blue, used only for the mark rather than the whole button. */
const FACEBOOK_BLUE = '#1877F2';

function FacebookMark() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill={FACEBOOK_BLUE}
    >
      <path d="M24 12.073C24 5.405 18.627 0 12 0S0 5.405 0 12.073C0 18.1 4.388 23.094 10.125 24v-8.437H7.078v-3.49h3.047V9.412c0-3.026 1.792-4.697 4.533-4.697 1.313 0 2.686.236 2.686.236v2.965H15.83c-1.49 0-1.955.93-1.955 1.886v2.271h3.328l-.532 3.49h-2.796V24C19.612 23.094 24 18.1 24 12.073z" />
    </svg>
  );
}

export default function FacebookLoginButton({
  onToken,
  onError,
  disabled = false,
  label = 'Continue with Facebook',
  theme = DEFAULT_THEME,
}: FacebookLoginButtonProps) {
  const { data: config, isLoading } = useFacebookLoginConfig();
  const [isBusy, setIsBusy] = useState(false);
  const [localError, setLocalError] = useState('');

  const fail = useCallback(
    (message: string) => {
      setLocalError(message);
      onError?.(message);
    },
    [onError]
  );

  const handleClick = useCallback(async () => {
    if (!config?.enabled) return;

    setLocalError('');
    setIsBusy(true);
    try {
      const fb = await loadFacebookSdk(config);

      // Meta's "Check Login Status" step: a person already connected to this
      // app does not need to be shown the dialog again.
      let response = await getFacebookLoginStatus(fb);
      if (response.status !== 'connected' || !response.authResponse?.accessToken) {
        response = await facebookLogin(fb, config);
      }

      if (response.status !== 'connected' || !response.authResponse?.accessToken) {
        // `not_authorized` and `unknown` both land here, as does closing the
        // dialog. None of them is an error worth alarming anyone about.
        fail('Facebook sign-in was not completed.');
        return;
      }

      await onToken(response.authResponse.accessToken);
    } catch (error) {
      console.error('Facebook sign-in error:', error);
      fail('Could not start Facebook sign-in. Please try again.');
    } finally {
      setIsBusy(false);
    }
  }, [config, fail, onToken]);

  // Hidden while the configuration is still unknown, and permanently when the
  // deployment has Facebook Login switched off.
  if (isLoading || !config?.enabled) {
    return null;
  }

  return (
    <div>
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled || isBusy}
        aria-busy={isBusy}
        className="w-full flex items-center justify-center gap-3 rounded-xl px-4 transition-opacity disabled:opacity-60 disabled:cursor-not-allowed"
        style={{
          minHeight: 48,
          background: theme.bgCard,
          border: `1px solid ${theme.border}`,
          color: theme.textPrimary,
          fontWeight: 600,
        }}
      >
        <FacebookMark />
        <span>{isBusy ? 'Connecting to Facebook…' : label}</span>
      </button>

      {localError ? (
        <p role="alert" className="mt-2 text-sm" style={{ color: theme.textMuted }}>
          {localError}
        </p>
      ) : null}
    </div>
  );
}
