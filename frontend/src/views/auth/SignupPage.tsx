/**
 * Sign up — Radix Themes redesign
 */

import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import {
  Box,
  Button,
  Callout,
  Card,
  Container,
  Flex,
  Heading,
  Text,
  TextField,
} from '@radix-ui/themes';
import { useSignup } from '@/api/auth';
import { useAuth } from '@/contexts/AuthContext';
import FacebookLoginButton from '@/components/auth/FacebookLoginButton';
import type { FacebookCredential } from '@/lib/facebookSdk';
import { SEO, pageSEO } from '@/components/common/SEO';
import { StratumThemeProvider } from '@/theme/StratumThemeProvider';
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
  } = useForm<SignupForm>({
    resolver: zodResolver(signupSchema),
  });

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
      // error surfaced via registerMutation.error
    }
  };

  const handleFacebookCredential = async (credential: FacebookCredential) => {
    setFacebookError('');
    setFacebookPending(true);
    try {
      const result = await loginWithFacebook(credential);
      if (result.success) {
        navigate('/dashboard/activation', { replace: true });
      } else {
        setFacebookError(result.error || 'Facebook sign-up failed');
      }
    } catch {
      setFacebookError('An unexpected error occurred');
    } finally {
      setFacebookPending(false);
    }
  };

  return (
    <StratumThemeProvider>
      <SEO {...pageSEO.signup} />
      <MarketingShell>
        <Container size="2" py="9">
          <Flex direction="column" align="center" gap="6">
            <Box style={{ textAlign: 'center', maxWidth: 480 }}>
              <Heading size="7" mb="2">
                Create your workspace
              </Heading>
              <Text color="gray" size="3">
                Free to start. After signup you&apos;ll connect OAuth, CAPI, and your Marketing
                API token — we walk you through each step.
              </Text>
            </Box>

            <Card size="4" style={{ width: '100%', maxWidth: 480 }}>
              <Flex direction="column" gap="4">
                {apiError && (
                  <Callout.Root color="red" size="1">
                    <Callout.Text>{apiError}</Callout.Text>
                  </Callout.Root>
                )}

                <form onSubmit={handleSubmit(onSubmit)}>
                  <Flex direction="column" gap="3">
                    <Box>
                      <Text as="label" size="2" weight="medium" mb="1">
                        Full name
                      </Text>
                      <TextField.Root
                        placeholder="Your name"
                        size="3"
                        {...register('name')}
                      />
                      {errors.name && (
                        <Text size="1" color="red" mt="1">
                          {errors.name.message}
                        </Text>
                      )}
                    </Box>
                    <Box>
                      <Text as="label" size="2" weight="medium" mb="1">
                        Work email
                      </Text>
                      <TextField.Root
                        type="email"
                        placeholder="you@company.com"
                        size="3"
                        {...register('email')}
                      />
                      {errors.email && (
                        <Text size="1" color="red" mt="1">
                          {errors.email.message}
                        </Text>
                      )}
                    </Box>
                    <Box>
                      <Text as="label" size="2" weight="medium" mb="1">
                        Company
                      </Text>
                      <TextField.Root
                        placeholder="Company name"
                        size="3"
                        {...register('company')}
                      />
                      {errors.company && (
                        <Text size="1" color="red" mt="1">
                          {errors.company.message}
                        </Text>
                      )}
                    </Box>
                    <Box>
                      <Text as="label" size="2" weight="medium" mb="1">
                        Password
                      </Text>
                      <TextField.Root type="password" size="3" {...register('password')} />
                      {errors.password && (
                        <Text size="1" color="red" mt="1">
                          {errors.password.message}
                        </Text>
                      )}
                    </Box>
                    <Box>
                      <Text as="label" size="2" weight="medium" mb="1">
                        Confirm password
                      </Text>
                      <TextField.Root type="password" size="3" {...register('confirmPassword')} />
                      {errors.confirmPassword && (
                        <Text size="1" color="red" mt="1">
                          {errors.confirmPassword.message}
                        </Text>
                      )}
                    </Box>
                    <Button type="submit" size="3" disabled={isLoading} style={{ width: '100%' }}>
                      {isLoading ? 'Creating account…' : 'Create account'}
                    </Button>
                  </Flex>
                </form>

                <Flex align="center" gap="3">
                  <Box style={{ flex: 1, height: 1, background: 'var(--gray-6)' }} />
                  <Text size="1" color="gray">
                    or
                  </Text>
                  <Box style={{ flex: 1, height: 1, background: 'var(--gray-6)' }} />
                </Flex>

                <FacebookLoginButton
                  onCredential={handleFacebookCredential}
                  disabled={isLoading}
                />

                <Text size="2" color="gray" align="center">
                  Already have an account?{' '}
                  <Link to="/login" style={{ color: 'var(--teal-11)' }}>
                    Sign in
                  </Link>
                </Text>
              </Flex>
            </Card>
          </Flex>
        </Container>
      </MarketingShell>
    </StratumThemeProvider>
  );
}
