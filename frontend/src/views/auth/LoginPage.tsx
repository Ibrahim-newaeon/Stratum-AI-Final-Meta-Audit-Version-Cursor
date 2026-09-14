/**
 * Login — Studio Pearl & Indigo / Navy & Periwinkle
 */

import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import FacebookLoginButton from '@/components/auth/FacebookLoginButton';
import type { FacebookCredential } from '@/lib/facebookSdk';
import { SEO, pageSEO } from '@/components/common/SEO';
import { MarketingShell } from '@/components/marketing/MarketingShell';

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login, loginWithFacebook } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const from = location.state?.from?.pathname || '/dashboard/activation';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);
    try {
      const result = await login(email, password);
      if (result.success) navigate(from, { replace: true });
      else setError(result.error || 'Login failed');
    } catch {
      setError('An unexpected error occurred');
    } finally {
      setIsLoading(false);
    }
  };

  const handleFacebookCredential = async (credential: FacebookCredential) => {
    setError('');
    setIsLoading(true);
    try {
      const result = await loginWithFacebook(credential);
      if (result.success) navigate(from, { replace: true });
      else setError(result.error || 'Facebook sign-in failed');
    } catch {
      setError('An unexpected error occurred');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <>
      <SEO {...pageSEO.login} />
      <MarketingShell>
        <div className="mx-auto max-w-md px-6 py-16">
          <div className="mb-8 text-center">
            <h1 className="text-3xl font-semibold" style={{ color: 'var(--studio-text)' }}>
              Welcome back
            </h1>
            <p className="mt-2 text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
              Sign in to your Stratum workspace. New users complete Meta Setup after login.
            </p>
          </div>

          <div className="studio-card p-6">
            {error && (
              <div
                className="mb-4 rounded-[10px] border px-3 py-2 text-sm"
                style={{
                  borderColor: 'var(--studio-border)',
                  color: '#DC2626',
                  background: 'rgba(220,38,38,0.06)',
                }}
              >
                {error}
              </div>
            )}
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="mb-1 block text-sm font-medium" style={{ color: 'var(--studio-text)' }}>
                  Email
                </label>
                <input
                  type="email"
                  required
                  className="studio-search w-full rounded-[10px] border px-3 py-2.5 text-sm"
                  style={{
                    background: 'var(--studio-search-bg)',
                    borderColor: 'var(--studio-border)',
                    color: 'var(--studio-text)',
                  }}
                  placeholder="you@company.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div>
                <div className="mb-1 flex justify-between">
                  <label className="text-sm font-medium" style={{ color: 'var(--studio-text)' }}>
                    Password
                  </label>
                  <Link to="/forgot-password" className="text-xs" style={{ color: 'var(--studio-accent)' }}>
                    Forgot?
                  </Link>
                </div>
                <input
                  type="password"
                  required
                  className="studio-search w-full rounded-[10px] border px-3 py-2.5 text-sm"
                  style={{
                    background: 'var(--studio-search-bg)',
                    borderColor: 'var(--studio-border)',
                    color: 'var(--studio-text)',
                  }}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              <button type="submit" className="studio-btn-primary w-full justify-center" disabled={isLoading}>
                {isLoading ? 'Signing in…' : 'Sign in'}
              </button>
            </form>

            <div className="my-5 flex items-center gap-3">
              <div className="h-px flex-1" style={{ background: 'var(--studio-border)' }} />
              <span className="text-xs" style={{ color: 'var(--studio-text-secondary)' }}>
                or
              </span>
              <div className="h-px flex-1" style={{ background: 'var(--studio-border)' }} />
            </div>

            <FacebookLoginButton onCredential={handleFacebookCredential} disabled={isLoading} />

            <p className="mt-5 text-center text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
              No account?{' '}
              <Link to="/signup" style={{ color: 'var(--studio-accent)' }}>
                Sign up free
              </Link>
            </p>
          </div>
        </div>
      </MarketingShell>
    </>
  );
}
