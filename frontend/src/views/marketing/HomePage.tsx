/**
 * Marketing home — Studio visual system (Pearl & Indigo / Navy & Periwinkle).
 */

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { BarChart3, Shield, Users, Zap } from 'lucide-react';
import { MarketingShell } from '@/components/marketing/MarketingShell';
import { SEO, pageSEO } from '@/components/common/SEO';
import { OnboardingChat, OnboardingChatButton } from '@/components/onboarding';

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
    description: 'Campaign insights, EMQ, and CAPI in one operating system built for Meta channels.',
  },
  {
    icon: Users,
    title: 'CDP + Custom Audiences',
    description:
      'Segments sync to Meta when you complete OAuth, CAPI, and your Marketing API token.',
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
    <>
      <SEO {...pageSEO.landing} />
      <MarketingShell>
        <section className="px-6 pb-16 pt-20">
          <div className="mx-auto max-w-[720px] text-center">
            <p
              className="mb-4 text-xs font-semibold uppercase tracking-[0.12em]"
              style={{ color: 'var(--studio-accent)' }}
            >
              Meta revenue operating system
            </p>
            <h1
              className="text-4xl font-semibold tracking-tight md:text-5xl"
              style={{ color: 'var(--studio-text)', lineHeight: 1.1 }}
            >
              Run Meta ads with{' '}
              <span style={{ color: 'var(--studio-accent)' }}>trust, not guesswork</span>
            </h1>
            <p className="mx-auto mt-5 max-w-xl text-base leading-7" style={{ color: 'var(--studio-text-secondary)' }}>
              Stratum connects OAuth, Conversions API, and Marketing API credentials upfront —
              so audiences, campaigns, and signals work together from day one.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <Link to="/signup" className="studio-btn-primary">
                Start free trial
              </Link>
              <Link to="/features" className="studio-btn-ghost">
                See features
              </Link>
            </div>
          </div>
        </section>

        <section className="px-6 pb-12">
          <div className="studio-card mx-auto max-w-[960px] p-6">
            <h2 className="text-lg font-semibold" style={{ color: 'var(--studio-text)' }}>
              Three Meta credentials — we guide you through all of them
            </h2>
            <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-3">
              {[
                ['1. OAuth', 'Connect Platforms for ads read & insights'],
                ['2. CAPI + Pixel', 'Server-side conversion events'],
                ['3. Marketing API token', 'System User for Custom Audiences — required'],
              ].map(([title, body]) => (
                <div key={title}>
                  <div className="text-sm font-semibold" style={{ color: 'var(--studio-text)' }}>
                    {title}
                  </div>
                  <div className="mt-1 text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
                    {body}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="px-6 pb-16">
          <h2
            className="mb-8 text-center text-2xl font-semibold"
            style={{ color: 'var(--studio-text)' }}
          >
            Built for Meta marketers who need control
          </h2>
          <div className="mx-auto grid max-w-[960px] grid-cols-1 gap-4 sm:grid-cols-2">
            {features.map(({ icon: Icon, title, description }) => (
              <div key={title} className="studio-card p-5">
                <Icon size={24} style={{ color: 'var(--studio-accent)' }} />
                <h3 className="mt-3 text-base font-semibold" style={{ color: 'var(--studio-text)' }}>
                  {title}
                </h3>
                <p className="mt-2 text-sm leading-6" style={{ color: 'var(--studio-text-secondary)' }}>
                  {description}
                </p>
              </div>
            ))}
          </div>
        </section>

        <section className="px-6 pb-24">
          <div className="studio-card mx-auto max-w-[640px] p-8 text-center">
            <h2 className="text-2xl font-semibold" style={{ color: 'var(--studio-text)' }}>
              Ready to connect Meta the right way?
            </h2>
            <p className="mt-2 text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
              Sign up and complete the Meta Setup checklist in minutes.
            </p>
            <Link to="/signup" className="studio-btn-primary mt-6 inline-flex">
              Create account
            </Link>
          </div>
        </section>
      </MarketingShell>

      {!chatOpen && <OnboardingChatButton onClick={() => setChatOpen(true)} pulse={true} />}
      <OnboardingChat isOpen={chatOpen} onClose={() => setChatOpen(false)} language="en" />
    </>
  );
}
