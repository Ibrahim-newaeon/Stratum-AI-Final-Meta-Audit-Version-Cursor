/**
 * Pricing Page
 * This portal is a free workspace: no payment gateway and no paid tiers.
 */

import { Link } from 'react-router-dom';
import { PageLayout } from '@/components/landing/PageLayout';
import { CheckIcon } from '@heroicons/react/24/outline';
import { pageSEO, SEO } from '@/components/common/SEO';

export const portalPricingPlan = {
  name: 'Free',
  price: '$0',
  period: '',
  description: 'Full workspace access for anyone who signs up. No credit card.',
  features: [
    'Unlimited team members',
    'Connect Meta (Facebook, Instagram, WhatsApp) read-only',
    'Signal Health scoring and Trust Gate',
    'Campaigns, rules, CAPI, and EMQ',
    'Trust Gate (Meta Autopilot writes off until enabled)',
    'No payment gateway and no subscription',
  ],
  cta: 'Create free account',
  href: '/signup',
};

export default function Pricing() {
  return (
    <PageLayout>
      <SEO {...pageSEO.pricing} />
      {/* Hero Section */}
      <section className="py-20 px-6">
        <div className="max-w-7xl mx-auto text-center">
          <h1
            className="text-4xl md:text-5xl lg:text-6xl font-bold mb-6"
            style={{ fontFamily: "'Inter', sans-serif" }}
          >
            <span className="text-white">Free for</span>
            <br />
            <span
              style={{
                background: 'linear-gradient(135deg, #a855f7 0%, #06b6d4 50%, #f97316 100%)',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
              }}
            >
              everyone
            </span>
          </h1>
          <p
            className="text-lg md:text-xl max-w-2xl mx-auto"
            style={{ color: 'rgba(255, 255, 255, 0.7)' }}
          >
            Create an account and use the workspace. There is no checkout, trial clock, or credit
            card.
          </p>
        </div>
      </section>

      {/* Pricing Card */}
      <section className="py-12 px-6">
        <div className="max-w-md mx-auto">
          <div
            className="relative p-8 rounded-3xl ring-2"
            style={{
              background:
                'linear-gradient(135deg, rgba(168, 85, 247, 0.1) 0%, rgba(6, 182, 212, 0.1) 100%)',
              border: '1px solid rgba(255, 255, 255, 0.08)',
            }}
          >
            <div className="mb-6">
              <h3 className="text-xl font-semibold text-white mb-2">{portalPricingPlan.name}</h3>
              <div className="flex items-baseline gap-1">
                <span className="text-4xl font-bold text-white">{portalPricingPlan.price}</span>
                <span style={{ color: 'rgba(255, 255, 255, 0.5)' }}>{portalPricingPlan.period}</span>
              </div>
              <p className="mt-3 text-sm" style={{ color: 'rgba(255, 255, 255, 0.6)' }}>
                {portalPricingPlan.description}
              </p>
            </div>

            <ul className="space-y-3 mb-8">
              {portalPricingPlan.features.map((feature) => (
                <li key={feature} className="flex items-start gap-3">
                  <CheckIcon
                    className="w-5 h-5 flex-shrink-0 mt-0.5"
                    style={{ color: '#34c759' }}
                  />
                  <span className="text-sm" style={{ color: 'rgba(255, 255, 255, 0.8)' }}>
                    {feature}
                  </span>
                </li>
              ))}
            </ul>

            <Link
              to={portalPricingPlan.href}
              className="block w-full py-3 px-6 rounded-xl text-center font-semibold transition-all"
              style={{
                background: '#f97316',
                color: '#ffffff',
                boxShadow: '0 4px 20px rgba(249, 115, 22, 0.4)',
              }}
            >
              {portalPricingPlan.cta}
            </Link>
          </div>
        </div>
      </section>

      {/* FAQ Section */}
      <section className="py-20 px-6">
        <div className="max-w-3xl mx-auto">
          <h2 className="text-3xl font-bold text-white text-center mb-12">
            Frequently Asked Questions
          </h2>
          <div className="space-y-6">
            {[
              {
                q: 'Do I need a credit card?',
                a: 'No. Signup creates a free workspace. This portal does not take payments.',
              },
              {
                q: 'Are there paid plans?',
                a: 'Not on this portal. Every account gets the same full workspace access.',
              },
              {
                q: 'Is there a trial that expires?',
                a: 'No. There is no trial clock and no subscription to cancel.',
              },
            ].map((faq) => (
              <div
                key={faq.q}
                className="p-6 rounded-2xl"
                style={{
                  background: 'rgba(255, 255, 255, 0.04)',
                  border: '1px solid rgba(255, 255, 255, 0.08)',
                }}
              >
                <h3 className="text-lg font-semibold text-white mb-2">{faq.q}</h3>
                <p className="text-sm" style={{ color: 'rgba(255, 255, 255, 0.6)' }}>
                  {faq.a}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>
    </PageLayout>
  );
}
