/**
 * Marketing site shell — Radix Themes header/footer for public pages.
 */

import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Box, Button, Container, Flex, Heading, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { Menu, X } from 'lucide-react';

const navLinks = [
  { name: 'Features', href: '/features' },
  { name: 'Pricing', href: '/pricing' },
  { name: 'Solutions', href: '/solutions/cdp' },
  { name: 'Resources', href: '/resources' },
  { name: 'Docs', href: '/docs' },
  { name: 'Company', href: '/about' },
];

interface MarketingShellProps {
  children: React.ReactNode;
}

export function MarketingShell({ children }: MarketingShellProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const { i18n } = useTranslation();

  useEffect(() => {
    document.documentElement.dir = i18n.language === 'ar' ? 'rtl' : 'ltr';
    document.documentElement.lang = i18n.language || 'en';
  }, [i18n.language]);

  const isActive = (href: string) =>
    href === '/solutions/cdp'
      ? location.pathname.startsWith('/solutions')
      : location.pathname === href || location.pathname.startsWith(`${href}/`);

  return (
    <Box style={{ minHeight: '100vh', background: '#0b1215' }}>
      <Box
        position="sticky"
        top="0"
        style={{
          zIndex: 50,
          backdropFilter: 'blur(12px)',
          background: 'rgba(11, 18, 21, 0.85)',
          borderBottom: '1px solid rgba(255,255,255,0.08)',
        }}
      >
        <Container size="4" py="3">
          <Flex align="center" justify="between" gap="4">
            <Link to="/" style={{ textDecoration: 'none', color: 'inherit' }}>
              <Flex align="center" gap="2">
                <Box
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: 10,
                    background: 'var(--teal-9)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontWeight: 700,
                    color: '#0b1215',
                  }}
                >
                  S
                </Box>
                <Heading size="4" weight="medium">
                  Stratum AI
                </Heading>
              </Flex>
            </Link>

            <Flex display={{ initial: 'none', md: 'flex' }} align="center" gap="5">
              {navLinks.map((link) => (
                <Link
                  key={link.href}
                  to={link.href}
                  style={{
                    textDecoration: 'none',
                    fontSize: 14,
                    color: isActive(link.href) ? 'var(--teal-11)' : 'var(--gray-11)',
                  }}
                >
                  {link.name}
                </Link>
              ))}
            </Flex>

            <Flex align="center" gap="2">
              <Button variant="ghost" size="2" asChild>
                <Link to="/login">Log in</Link>
              </Button>
              <Button size="2" asChild>
                <Link to="/signup">Start free</Link>
              </Button>
              <Box display={{ initial: 'block', md: 'none' }}>
                <Button
                  variant="ghost"
                  size="1"
                  onClick={() => setMobileOpen((o) => !o)}
                  aria-label="Toggle menu"
                >
                  {mobileOpen ? <X size={18} /> : <Menu size={18} />}
                </Button>
              </Box>
            </Flex>
          </Flex>

          {mobileOpen && (
            <Flex direction="column" gap="2" pt="3" display={{ md: 'none' }}>
              {navLinks.map((link) => (
                <Link
                  key={link.href}
                  to={link.href}
                  onClick={() => setMobileOpen(false)}
                  style={{
                    padding: '8px 0',
                    textDecoration: 'none',
                    color: isActive(link.href) ? 'var(--teal-11)' : 'var(--gray-11)',
                  }}
                >
                  {link.name}
                </Link>
              ))}
            </Flex>
          )}
        </Container>
      </Box>

      <Box asChild>{children}</Box>

      <Box
        style={{
          borderTop: '1px solid rgba(255,255,255,0.08)',
          background: 'rgba(0,0,0,0.3)',
          marginTop: 80,
        }}
      >
        <Container size="4" py="6">
          <Flex direction={{ initial: 'column', sm: 'row' }} justify="between" gap="4">
            <Text size="2" color="gray">
              © {new Date().getFullYear()} Stratum AI — Trust-Gated Autopilot for Meta channels
            </Text>
            <Flex gap="4">
              <Link to="/privacy" style={{ color: 'var(--gray-11)', fontSize: 14 }}>
                Privacy
              </Link>
              <Link to="/terms" style={{ color: 'var(--gray-11)', fontSize: 14 }}>
                Terms
              </Link>
              <Link to="/contact" style={{ color: 'var(--gray-11)', fontSize: 14 }}>
                Contact
              </Link>
            </Flex>
          </Flex>
        </Container>
      </Box>
    </Box>
  );
}
