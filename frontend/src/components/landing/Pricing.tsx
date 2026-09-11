/**
 * Landing Pricing Section (marketing only)
 *
 * This portal is a free workspace: CTAs go to signup. There is no payment
 * overlay on public pages or in Settings.
 */

import { useNavigate } from 'react-router-dom';
import { CheckIcon, SparklesIcon } from '@heroicons/react/24/outline';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { type PricingTier, type TrustBadge, usePricingTiers, useTrustBadges } from '@/api/cms';

// Fallback pricing data when CMS content is not available
const fallbackPlans: PricingTier[] = [
  {
    id: 'free',
    name: 'Free',
    description: 'Full workspace access for anyone who signs up',
    price: '$0',
    period: '',
    adSpend: 'No spend cap on this portal',
    features: [
      'Unlimited ad accounts',
      'Signal health monitoring',
      'Trust Gate (Meta Autopilot writes off until enabled)',
      'Campaigns, rules, CAPI, and EMQ',
      'No payment gateway',
      'No credit card',
    ],
    cta: 'Create free account',
    ctaLink: '/signup',
    highlighted: true,
    badge: 'Free',
    displayOrder: 0,
  },
];

const fallbackTrustBadges: TrustBadge[] = [
  { id: '1', icon: '🔒', text: 'SOC 2 Compliant', displayOrder: 0 },
  { id: '2', icon: '🛡️', text: 'GDPR Ready', displayOrder: 1 },
  { id: '3', icon: '💳', text: 'No credit card', displayOrder: 2 },
  { id: '4', icon: '🆓', text: 'Free workspace', displayOrder: 3 },
];

// Stratum Gold Dark theme
const theme = {
  gold: '#00c7be',
  goldLight: 'rgba(0, 199, 190, 0.15)',
  green: '#34c759', // Stratum Green
  bgBase: '#000000',
  bgCard: 'rgba(255, 255, 255, 0.03)',
  textPrimary: '#FFFFFF',
  textSecondary: 'rgba(255, 255, 255, 0.7)',
  textMuted: 'rgba(255, 255, 255, 0.5)',
  border: 'rgba(255, 255, 255, 0.08)',
  borderHover: 'rgba(255, 255, 255, 0.15)',
};

export function Pricing() {
  const navigate = useNavigate();

  const { isLoading: plansLoading } = usePricingTiers();
  const { data: cmsBadges, isLoading: badgesLoading } = useTrustBadges();

  // Paid CMS catalogue is ignored: this portal is a single free workspace.
  const plans = fallbackPlans;
  const trustBadges = cmsBadges && cmsBadges.length > 0 ? cmsBadges : fallbackTrustBadges;

  const isLoading = plansLoading || badgesLoading;

  return (
    <section className="py-32" id="pricing" style={{ background: theme.bgBase }}>
      <div className="max-w-7xl mx-auto px-6">
        {/* Section header */}
        <div className="text-center mb-16">
          <Badge
            variant="outline"
            className="mb-4 px-4 py-1 border-0"
            style={{
              background: theme.goldLight,
              color: theme.gold,
            }}
          >
            Pricing
          </Badge>
          <h2 className="text-4xl md:text-5xl font-bold text-white mb-4">
            Simple, Transparent Pricing
          </h2>
          <p className="text-lg max-w-2xl mx-auto" style={{ color: theme.textMuted }}>
            This portal is free. Create an account with full workspace access — no credit card
            and no payment gateway.
          </p>
        </div>

        {/* Pricing cards */}
        <div
          className={`grid md:grid-cols-1 gap-8 max-w-md mx-auto ${isLoading ? 'opacity-50' : ''}`}
        >
          {plans.map((plan) => (
            <Card
              key={plan.id}
              className="relative flex flex-col rounded-3xl border-0 transition-all duration-300 hover:-translate-y-1"
              style={{
                background: plan.highlighted
                  ? 'linear-gradient(to bottom, rgba(0, 199, 190, 0.1), rgba(255, 255, 255, 0.03))'
                  : theme.bgCard,
                backdropFilter: 'blur(40px)',
                WebkitBackdropFilter: 'blur(40px)',
                border: plan.highlighted
                  ? '2px solid rgba(0, 199, 190, 0.3)'
                  : `1px solid ${theme.border}`,
                boxShadow: plan.highlighted ? '0 0 40px rgba(0, 199, 190, 0.15)' : 'none',
              }}
            >
              {/* Badge */}
              {plan.badge && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2 z-10">
                  <Badge
                    className="text-white border-0 px-4 py-1"
                    style={{ background: theme.gold }}
                  >
                    <SparklesIcon className="w-3 h-3 mr-1" />
                    {plan.badge}
                  </Badge>
                </div>
              )}

              <CardHeader className="pb-4">
                <CardTitle className="text-2xl text-white">{plan.name}</CardTitle>
                <CardDescription style={{ color: theme.textMuted }}>{plan.description}</CardDescription>
              </CardHeader>

              <CardContent className="flex-1">
                {/* Price */}
                <div className="mb-4">
                  <div className="flex items-baseline gap-1">
                    <span className="text-5xl font-bold text-white">{plan.price}</span>
                    <span className="text-lg" style={{ color: theme.textMuted }}>{plan.period}</span>
                  </div>
                  <p className="text-sm mt-1" style={{ color: theme.textMuted }}>{plan.adSpend}</p>
                </div>

                <Separator className="my-6" style={{ background: theme.border }} />

                {/* Features */}
                <ul className="space-y-3">
                  {plan.features.map((feature, index) => (
                    <li key={index} className="flex items-start gap-3">
                      <CheckIcon className="w-5 h-5 flex-shrink-0 mt-0.5" style={{ color: theme.green }} />
                      <span className="text-sm" style={{ color: theme.textSecondary }}>{feature}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>

              <CardFooter className="pt-6">
                <Button
                  onClick={() =>
                    navigate(plan.ctaLink || (plan.name === 'Enterprise' ? '/contact' : '/signup'))
                  }
                  className="w-full py-6 text-base font-semibold rounded-2xl transition-all"
                  style={
                    plan.highlighted
                      ? {
                          background: theme.gold,
                          color: '#FFFFFF',
                          boxShadow: '0 0 30px rgba(0, 199, 190, 0.3)',
                        }
                      : {
                          background: 'rgba(255, 255, 255, 0.05)',
                          color: '#FFFFFF',
                          border: `1px solid ${theme.border}`,
                        }
                  }
                  size="lg"
                >
                  {plan.cta}
                </Button>
              </CardFooter>
            </Card>
          ))}
        </div>

        {/* Trust Badges */}
        <div className="mt-16 flex flex-wrap justify-center gap-4">
          {trustBadges.map((badge) => (
            <Badge
              key={badge.id}
              variant="outline"
              className="px-4 py-2 text-sm border-0"
              style={{
                background: theme.bgCard,
                backdropFilter: 'blur(20px)',
                border: `1px solid ${theme.border}`,
                color: theme.textMuted,
              }}
            >
              <span className="mr-2">{badge.icon}</span>
              {badge.text}
            </Badge>
          ))}
        </div>

        {/* Comparison teaser */}
        <div className="mt-16 text-center">
          <Card
            className="inline-block px-8 py-6 rounded-2xl border-0"
            style={{
              background: theme.bgCard,
              backdropFilter: 'blur(40px)',
              WebkitBackdropFilter: 'blur(40px)',
              border: `1px solid ${theme.border}`,
            }}
          >
            <div className="flex items-center gap-4">
              <div className="text-left">
                <p className="text-white font-medium">Not sure which plan is right for you?</p>
                <p className="text-sm" style={{ color: theme.textMuted }}>
                  Talk to our team for a personalized recommendation.
                </p>
              </div>
              <Button
                variant="outline"
                className="rounded-xl border-0"
                style={{
                  background: theme.goldLight,
                  color: theme.gold,
                  border: `1px solid rgba(0, 199, 190, 0.3)`,
                }}
                onClick={() => navigate('/contact')}
              >
                Contact Sales
              </Button>
            </div>
          </Card>
        </div>
      </div>
    </section>
  );
}
