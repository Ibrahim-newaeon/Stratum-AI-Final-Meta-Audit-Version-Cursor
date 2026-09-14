/**
 * Sign up — Studio Pearl & Indigo / Navy & Periwinkle
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
import { MarketingShell } from '@/components/marketing/MarketingShell';

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
      // surfaced via mutation error
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

  const fieldStyle = {
    background: 'var(--studio-search-bg)',
    borderColor: 'var(--studio-border)',
    color: 'var(--studio-text)',
  } as const;

  return (
    <>
      <SEO {...pageSEO.signup} />
      <MarketingShell>
        <div className="mx-auto max-w-md px-6 py-16">
          <div className="mb-8 text-center">
            <h1 className="text-3xl font-semibold" style={{ color: 'var(--studio-text)' }}>
              Create your workspace
            </h1>
            <p className="mt-2 text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
              Free to start. After signup you&apos;ll connect OAuth, CAPI, and your Marketing API
              token.
            </p>
          </div>

          <div className="studio-card p-6">
            {apiError && (
              <div
                className="mb-4 rounded-[10px] border px-3 py-2 text-sm"
                style={{ borderColor: 'var(--studio-border)', color: '#DC2626' }}
              >
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
                  <label className="mb-1 block text-sm font-medium" style={{ color: 'var(--studio-text)' }}>
                    {label}
                  </label>
                  <input
                    type={type}
                    className="studio-search w-full rounded-[10px] border px-3 py-2.5 text-sm"
                    style={fieldStyle}
                    {...register(key)}
                  />
                  {errors[key] && (
                    <p className="mt-1 text-xs text-red-600">{errors[key]?.message}</p>
                  )}
                </div>
              ))}
              <button type="submit" className="studio-btn-primary w-full justify-center" disabled={isLoading}>
                {isLoading ? 'Creating account…' : 'Create account'}
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
              Already have an account?{' '}
              <Link to="/login" style={{ color: 'var(--studio-accent)' }}>
                Sign in
              </Link>
            </p>
          </div>
        </div>
      </MarketingShell>
    </>
  );
}
