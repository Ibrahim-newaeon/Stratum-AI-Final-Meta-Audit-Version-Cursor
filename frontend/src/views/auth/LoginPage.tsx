/**
 * Login — Evidence Room
 */

import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import FacebookLoginButton from '@/components/auth/FacebookLoginButton';
import type { FacebookCredential } from '@/lib/facebookSdk';
import { SEO, pageSEO } from '@/components/common/SEO';
import { EvidenceMarketingShell } from '@/components/evidence/EvidenceMarketingShell';

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login, loginWithFacebook } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const from = location.state?.from?.pathname || '/dashboard/overview';

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

  const field = {
    background: 'var(--er-bg)',
    borderColor: 'var(--er-border)',
    color: 'var(--er-text)',
    borderRadius: 'var(--er-radius)',
  } as const;

  return (
    <>
      <SEO {...pageSEO.login} />
      <EvidenceMarketingShell>
        <div className="mx-auto max-w-md px-6 py-16">
          <p className="er-label text-center">Sign in</p>
          <h1 className="er-serif mt-2 text-center text-4xl">Welcome back</h1>
          <p className="mt-3 text-center text-sm" style={{ color: 'var(--er-muted)' }}>
            Enter the evidence room. Complete Meta Setup after login if integrations are incomplete.
          </p>
          <div className="er-panel mt-8 p-6">
            {error && (
              <div className="mb-4 border p-3 text-sm" style={{ borderColor: 'var(--er-critical)', color: 'var(--er-critical)' }}>
                {error}
              </div>
            )}
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="mb-1 block text-sm font-medium">Email</label>
                <input type="email" required className="w-full border px-3 py-2.5 text-sm" style={field} value={email} onChange={(e) => setEmail(e.target.value)} />
              </div>
              <div>
                <div className="mb-1 flex justify-between">
                  <label className="text-sm font-medium">Password</label>
                  <Link to="/forgot-password" className="text-xs" style={{ color: 'var(--er-accent)' }}>Forgot?</Link>
                </div>
                <input type="password" required className="w-full border px-3 py-2.5 text-sm" style={field} value={password} onChange={(e) => setPassword(e.target.value)} />
              </div>
              <button type="submit" className="er-btn-primary w-full" disabled={isLoading}>
                {isLoading ? 'Signing in…' : 'Sign in'}
              </button>
            </form>
            <div className="my-5 flex items-center gap-3">
              <div className="h-px flex-1" style={{ background: 'var(--er-border)' }} />
              <span className="text-xs" style={{ color: 'var(--er-muted)' }}>or</span>
              <div className="h-px flex-1" style={{ background: 'var(--er-border)' }} />
            </div>
            <FacebookLoginButton onCredential={handleFacebookCredential} disabled={isLoading} />
            <p className="mt-5 text-center text-sm" style={{ color: 'var(--er-muted)' }}>
              No account? <Link to="/signup" style={{ color: 'var(--er-accent)' }}>Start free trial</Link>
            </p>
          </div>
        </div>
      </EvidenceMarketingShell>
    </>
  );
}
