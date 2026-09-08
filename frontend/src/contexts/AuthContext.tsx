/**
 * Authentication Context
 * Manages user authentication state across the application
 */

import { createContext, ReactNode, useContext, useEffect, useState } from 'react';
import type { FacebookCredential } from '@/lib/facebookSdk';

export interface User {
  id: string;
  email: string;
  name: string;
  role: 'superadmin' | 'admin' | 'user';
  avatar?: string;
  organization?: string;
  permissions: string[];
  tenant_id?: number | null;
}

/**
 * Outcome of a sign-in attempt.
 *
 * `mfaRequired` is its own state rather than an error: the credentials were
 * accepted and a second factor is outstanding. `mfaSessionToken` is what
 * `POST /auth/login/mfa` needs to finish, and it is deliberately not persisted
 * anywhere — it lives for five minutes and belongs to this attempt only.
 */
export interface LoginResult {
  success: boolean;
  error?: string;
  mfaRequired?: boolean;
  mfaSessionToken?: string;
}

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<LoginResult>;
  /**
   * Sign in with the access token Facebook's JS SDK produced.
   *
   * The token is posted to the backend, which verifies it against our own Meta
   * app before it resolves an account. Nothing the SDK reports about who the
   * person is — `userID` in particular — is sent or trusted here.
   */
  loginWithFacebook: (credential: FacebookCredential) => Promise<LoginResult>;
  logout: () => void;
  updateUser: (userData: Partial<User>) => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const AUTH_STORAGE_KEY = 'stratum_auth';
const ACCESS_TOKEN_KEY = 'access_token';
const REFRESH_TOKEN_KEY = 'refresh_token';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Check for existing session on mount
  useEffect(() => {
    const stored = localStorage.getItem(AUTH_STORAGE_KEY);
    if (stored) {
      try {
        const parsedUser = JSON.parse(stored);
        setUser(parsedUser);
      } catch (e) {
        localStorage.removeItem(AUTH_STORAGE_KEY);
      }
    }
    setIsLoading(false);
  }, []);

  /**
   * Turn an API error body into one sentence.
   *
   * FastAPI answers with `detail` as either a string or an array of Pydantic
   * validation errors, so both shapes have to be unwrapped before anything is
   * shown to a person.
   */
  const errorMessageFrom = (data: unknown, fallback: string): string => {
    const detail = (data as { detail?: unknown })?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string; message?: string };
      return first.msg || first.message || 'Validation error';
    }
    if (detail && typeof detail === 'object') {
      const msg = (detail as { msg?: string }).msg;
      if (msg) return msg;
    }
    return fallback;
  };

  /**
   * Persist the tokens from a successful sign-in and load the profile.
   *
   * Shared by every sign-in method so a Facebook session and a password session
   * are established identically — same storage keys, same `/auth/me` lookup,
   * same fallback when that lookup fails.
   */
  const establishSession = async (
    accessToken: string,
    refreshToken: string | null | undefined,
    fallbackEmail: string
  ): Promise<LoginResult> => {
    localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
    if (refreshToken) {
      localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
    }

    const userResponse = await fetch('/api/v1/auth/me', {
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
    });

    if (userResponse.ok) {
      const userData = await userResponse.json();
      const userInfo: User = {
        id: String(userData.data.id),
        email: userData.data.email,
        name: userData.data.full_name || userData.data.email,
        role: userData.data.role || 'user',
        permissions: ['all'],
        // `/auth/me` returns this and the field has always been declared, but
        // nothing populated it - so every consumer read `undefined` and any
        // "is this my own tenant?" check silently answered no.
        tenant_id: userData.data.tenant_id ?? null,
      };
      setUser(userInfo);
      localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(userInfo));
    } else {
      // Fallback if /me fails - keep the session, with what we already know.
      const userInfo: User = {
        id: '1',
        email: fallbackEmail,
        name: fallbackEmail ? fallbackEmail.split('@')[0] : 'Account',
        role: 'admin',
        permissions: ['all'],
      };
      setUser(userInfo);
      localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(userInfo));
    }

    return { success: true };
  };

  const login = async (email: string, password: string): Promise<LoginResult> => {
    try {
      // Call the actual backend API
      const response = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email, password }),
      });

      const data = await response.json();

      if (!response.ok) {
        return { success: false, error: errorMessageFrom(data, 'Login failed') };
      }

      // A second factor is outstanding: no tokens were issued, so there is no
      // session to establish yet.
      if (data.data?.mfa_required) {
        return {
          success: false,
          mfaRequired: true,
          mfaSessionToken: data.data.mfa_session_token,
          error: 'Enter your authentication code to finish signing in.',
        };
      }

      if (!data.data?.access_token) {
        return { success: false, error: 'Login failed' };
      }

      return await establishSession(data.data.access_token, data.data.refresh_token, email);
    } catch (error) {
      console.error('Login error:', error);
      return { success: false, error: 'Network error. Please try again.' };
    }
  };

  const loginWithFacebook = async (credential: FacebookCredential): Promise<LoginResult> => {
    try {
      const response = await fetch('/api/v1/auth/facebook', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        // Only the credential is sent - a Login-for-Business `code` or a
        // classic `access_token`. The SDK's `userID` is not evidence of
        // anything, so the backend re-derives the identity from Meta itself.
        body: JSON.stringify(credential),
      });

      const data = await response.json();

      if (!response.ok) {
        return {
          success: false,
          error: errorMessageFrom(data, 'Facebook sign-in failed'),
        };
      }

      if (data.data?.mfa_required) {
        return {
          success: false,
          mfaRequired: true,
          mfaSessionToken: data.data.mfa_session_token,
          error: 'Enter your authentication code to finish signing in.',
        };
      }

      if (!data.data?.access_token) {
        return { success: false, error: 'Facebook sign-in failed' };
      }

      // No email is known client-side here: the account may not have one, and
      // the backend never echoes it. `/auth/me` supplies it in the normal case.
      return await establishSession(data.data.access_token, data.data.refresh_token, '');
    } catch (error) {
      console.error('Facebook login error:', error);
      return { success: false, error: 'Network error. Please try again.' };
    }
  };

  const logout = () => {
    setUser(null);
    localStorage.removeItem(AUTH_STORAGE_KEY);
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
  };

  const updateUser = (userData: Partial<User>) => {
    if (user) {
      const updatedUser = { ...user, ...userData };
      setUser(updatedUser);
      localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(updatedUser));
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        isLoading,
        login,
        loginWithFacebook,
        logout,
        updateUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
