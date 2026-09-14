/**
 * Login — Radix Themes redesign
 */

import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
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
import { useAuth } from '@/contexts/AuthContext';
import FacebookLoginButton from '@/components/auth/FacebookLoginButton';
import type { FacebookCredential } from '@/lib/facebookSdk';
import { SEO, pageSEO } from '@/components/common/SEO';
import { StratumThemeProvider } from '@/theme/StratumThemeProvider';
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
      if (result.success) {
        navigate(from, { replace: true });
      } else {
        setError(result.error || 'Login failed');
      }
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
      if (result.success) {
        navigate(from, { replace: true });
      } else {
        setError(result.error || 'Facebook sign-in failed');
      }
    } catch {
      setError('An unexpected error occurred');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <StratumThemeProvider>
      <SEO {...pageSEO.login} />
      <MarketingShell>
        <Container size="2" py="9">
          <Flex direction="column" align="center" gap="6">
            <Box style={{ textAlign: 'center', maxWidth: 420 }}>
              <Heading size="7" mb="2">
                Welcome back
              </Heading>
              <Text color="gray" size="3">
                Sign in to your Stratum workspace. New users complete Meta Setup after login.
              </Text>
            </Box>

            <Card size="4" style={{ width: '100%', maxWidth: 420 }}>
              <Flex direction="column" gap="4">
                {error && (
                  <Callout.Root color="red" size="1">
                    <Callout.Text>{error}</Callout.Text>
                  </Callout.Root>
                )}

                <form onSubmit={handleSubmit}>
                  <Flex direction="column" gap="3">
                    <Box>
                      <Text as="label" size="2" weight="medium" mb="1">
                        Email
                      </Text>
                      <TextField.Root
                        type="email"
                        required
                        placeholder="you@company.com"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        size="3"
                      />
                    </Box>
                    <Box>
                      <Flex justify="between" align="center" mb="1">
                        <Text as="label" size="2" weight="medium">
                          Password
                        </Text>
                        <Link to="/forgot-password" style={{ fontSize: 13, color: 'var(--teal-11)' }}>
                          Forgot?
                        </Link>
                      </Flex>
                      <TextField.Root
                        type="password"
                        required
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        size="3"
                      />
                    </Box>
                    <Button type="submit" size="3" disabled={isLoading} style={{ width: '100%' }}>
                      {isLoading ? 'Signing in…' : 'Sign in'}
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
                  No account?{' '}
                  <Link to="/signup" style={{ color: 'var(--teal-11)' }}>
                    Sign up free
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
