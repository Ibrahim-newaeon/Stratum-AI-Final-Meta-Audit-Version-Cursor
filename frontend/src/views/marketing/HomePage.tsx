/**
 * Marketing home page — React + Radix Themes (replaces iframe landing.html).
 */

import { Link } from 'react-router-dom';
import {
  Box,
  Button,
  Card,
  Container,
  Flex,
  Grid,
  Heading,
  Section,
  Text,
} from '@radix-ui/themes';
import { MarketingShell } from '@/components/marketing/MarketingShell';
import { StratumThemeProvider } from '@/theme/StratumThemeProvider';
import { SEO, pageSEO } from '@/components/common/SEO';
import { OnboardingChat, OnboardingChatButton } from '@/components/onboarding';
import { useState } from 'react';
import { Shield, Zap, BarChart3, Users } from 'lucide-react';

const features = [
  {
    icon: Shield,
    title: 'Trust-Gated Autopilot',
    description:
      'Automated Meta actions run only when signal health passes the gate — no blind optimization.',
  },
  {
    icon: BarChart3,
    title: 'Revenue intelligence',
    description:
      'Campaign insights, EMQ, and CAPI in one operating system built for Meta channels.',
  },
  {
    icon: Users,
    title: 'CDP + Custom Audiences',
    description:
      'Segments sync to Meta when you provide OAuth, CAPI, and your Marketing API token.',
  },
  {
    icon: Zap,
    title: 'Rules & automation',
    description:
      'Local automation rules with transparent trust scoring — Meta writes when you enable Autopilot.',
  },
];

export default function HomePage() {
  const [chatOpen, setChatOpen] = useState(false);

  return (
    <StratumThemeProvider>
      <SEO {...pageSEO.landing} />
      <MarketingShell>
        {/* Hero */}
        <Section size="3" style={{ paddingTop: 80, paddingBottom: 80 }}>
          <Container size="3">
            <Flex direction="column" align="center" gap="5" style={{ textAlign: 'center' }}>
              <Text size="2" color="teal" weight="medium" style={{ letterSpacing: '0.08em' }}>
                META REVENUE OPERATING SYSTEM
              </Text>
              <Heading
                size="9"
                weight="bold"
                style={{ maxWidth: 720, lineHeight: 1.1 }}
              >
                Run Meta ads with{' '}
                <span style={{ color: 'var(--teal-11)' }}>trust, not guesswork</span>
              </Heading>
              <Text size="4" color="gray" style={{ maxWidth: 560, lineHeight: 1.6 }}>
                Stratum connects OAuth, Conversions API, and Marketing API credentials upfront —
                so audiences, campaigns, and signals work together from day one.
              </Text>
              <Flex gap="3" wrap="wrap" justify="center" mt="2">
                <Button size="3" asChild>
                  <Link to="/signup">Start free trial</Link>
                </Button>
                <Button size="3" variant="outline" asChild>
                  <Link to="/features">See features</Link>
                </Button>
              </Flex>
            </Flex>
          </Container>
        </Section>

        {/* Integration callout */}
        <Section size="2">
          <Container size="3">
            <Card size="3" style={{ background: 'rgba(0, 199, 190, 0.08)', borderColor: 'rgba(0,199,190,0.25)' }}>
              <Flex direction="column" gap="3">
                <Heading size="4">Three Meta credentials — we guide you through all of them</Heading>
                <Grid columns={{ initial: '1', md: '3' }} gap="4">
                  <Box>
                    <Text weight="bold" size="2">1. OAuth</Text>
                    <Text size="2" color="gray">Connect Platforms for ads read & insights</Text>
                  </Box>
                  <Box>
                    <Text weight="bold" size="2">2. CAPI + Pixel</Text>
                    <Text size="2" color="gray">Server-side conversion events</Text>
                  </Box>
                  <Box>
                    <Text weight="bold" size="2">3. Marketing API token</Text>
                    <Text size="2" color="gray">System User for Custom Audiences — required</Text>
                  </Box>
                </Grid>
              </Flex>
            </Card>
          </Container>
        </Section>

        {/* Features */}
        <Section size="3">
          <Container size="3">
            <Heading size="6" mb="6" align="center">
              Built for Meta marketers who need control
            </Heading>
            <Grid columns={{ initial: '1', sm: '2' }} gap="4">
              {features.map(({ icon: Icon, title, description }) => (
                <Card key={title} size="3">
                  <Flex direction="column" gap="3">
                    <Box style={{ color: 'var(--teal-11)' }}>
                      <Icon size={28} />
                    </Box>
                    <Heading size="4">{title}</Heading>
                    <Text size="2" color="gray">
                      {description}
                    </Text>
                  </Flex>
                </Card>
              ))}
            </Grid>
          </Container>
        </Section>

        {/* CTA */}
        <Section size="3" style={{ paddingBottom: 100 }}>
          <Container size="2">
            <Card size="4" style={{ textAlign: 'center' }}>
              <Flex direction="column" align="center" gap="4">
                <Heading size="6">Ready to connect Meta the right way?</Heading>
                <Text color="gray">Sign up and complete the Meta Setup checklist in minutes.</Text>
                <Button size="3" asChild>
                  <Link to="/signup">Create account</Link>
                </Button>
              </Flex>
            </Card>
          </Container>
        </Section>
      </MarketingShell>

      {!chatOpen && (
        <OnboardingChatButton onClick={() => setChatOpen(true)} pulse={true} />
      )}
      <OnboardingChat isOpen={chatOpen} onClose={() => setChatOpen(false)} language="en" />
    </StratumThemeProvider>
  );
}
