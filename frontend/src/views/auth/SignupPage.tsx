/**
 * Sign up — Evidence Room
 */

import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useSignup } from '@/api/auth';
import { useAuth } from '@/contexts/AuthContext';
import FacebookLoginButton from '@/components/auth/FacebookLoginButton';
import type { FacebookCredential } from '@/lib/facebookSdk';
import { SEO, pageSEO } from '@/components/common/SEO';
import { EvidenceMarketingShell } from '@/components/evidence/EvidenceMarketingShell';

const signupSchema = z
  .object({
    name: z.string().min(2, 'Name must be at least 2 characters'),
    email: z.string().email('Please enter a valid email'),
    company: z.string().min(2, 'Company name is required'),
    password: z.string().min(8, 'Password must be at least 8 characters'),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords don't match",
    path: ['confirmPassword'],
  });

type SignupForm = z.infer<typeof signupSchema>;

export default function SignupPage() {
  const navigate = useNavigate();
  const { loginWithFacebook } = useAuth();
  const [facebookError, setFacebookError] = useState('');
  const [facebookPending, setFacebookPending] = useState(false);
  const registerMutation = useSignup();
  const isLoading = registerMutation.isPending || facebookPending;
  const apiError = facebookError || registerMutation.error?.message;

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<SignupForm>({ resolver: zodResolver(signupSchema) });

  const onSubmit = async (data: SignupForm) => {
    try {
      await registerMutation.mutateAsync({
        full_name: data.name,
        email: data.email,
        company_name: data.company,
        password: data.password,
      });
      navigate('/login', { state: { registered: true } });
    } catch {
      /* mutation error */
    }
  };

  const handleFacebookCredential = async (credential: FacebookCredential) => {
    setFacebookError('');
    setFacebookPending(true);
    try {
      const result = await loginWithFacebook(credential);
      if (result.success) navigate('/dashboard/activation', { replace: true });
      else setFacebookError(result.error || 'Facebook sign-up failed');
    } catch {
      setFacebookError('An unexpected error occurred');
    } finally {
      setFacebookPending(false);
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
      <SEO {...pageSEO.signup} />
      <EvidenceMarketingShell>
        <div className="mx-auto max-w-md px-6 py-16">
          <p className="er-label text-center">Start free trial</p>
          <h1 className="er-serif mt-2 text-center text-4xl">Create your workspace</h1>
          <p className="mt-3 text-center text-sm" style={{ color: 'var(--er-muted)' }}>
            Start with a read-only review. Connect data. Inspect the first decision trail.
          </p>
          <div className="er-panel mt-8 p-6">
            {apiError && (
              <div className="mb-4 border p-3 text-sm" style={{ borderColor: 'var(--er-critical)', color: 'var(--er-critical)' }}>
                {apiError}
              </div>
            )}
            <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
              {(
                [
                  ['name', 'Full name', 'text'],
                  ['email', 'Work email', 'email'],
                  ['company', 'Company', 'text'],
                  ['password', 'Password', 'password'],
                  ['confirmPassword', 'Confirm password', 'password'],
                ] as const
              ).map(([key, label, type]) => (
                <div key={key}>
                  <label className="mb-1 block text-sm font-medium">{label}</label>
                  <input type={type} className="w-full border px-3 py-2.5 text-sm" style={field} {...register(key)} />
                  {errors[key] && <p className="mt-1 text-xs" style={{ color: 'var(--er-critical)' }}>{errors[key]?.message}</p>}
                </div>
              ))}
              <button type="submit" className="er-btn-primary w-full" disabled={isLoading}>
                {isLoading ? 'Creating…' : 'Create account'}
              </button>
            </form>
            <div className="my-5 flex items-center gap-3">
              <div className="h-px flex-1" style={{ background: 'var(--er-border)' }} />
              <span className="text-xs" style={{ color: 'var(--er-muted)' }}>or</span>
              <div className="h-px flex-1" style={{ background: 'var(--er-border)' }} />
            </div>
            <FacebookLoginButton onCredential={handleFacebookCredential} disabled={isLoading} />
            <p className="mt-5 text-center text-sm" style={{ color: 'var(--er-muted)' }}>
              Already have an account? <Link to="/login" style={{ color: 'var(--er-accent)' }}>Sign in</Link>
            </p>
          </div>
        </div>
      </EvidenceMarketingShell>
    </>
  );
}
