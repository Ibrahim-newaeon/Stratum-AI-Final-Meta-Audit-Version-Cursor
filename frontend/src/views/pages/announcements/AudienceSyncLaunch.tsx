/**
 * Audience Sync Launch Announcement Page
 * Linked from the landing page announcement strip
 */

import { Link } from 'react-router-dom';
import { PageLayout } from '@/components/landing/PageLayout';
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  ChartBarIcon,
  ClockIcon,
  CloudArrowUpIcon,
  ShieldCheckIcon,
  UserGroupIcon,
} from '@heroicons/react/24/outline';

const platforms = [
  {
    name: 'Facebook',
    logo: (
      <svg className="w-8 h-8" viewBox="0 0 24 24" fill="currentColor">
        <path d="M24 12.07C24 5.4 18.63 0 12 0S0 5.4 0 12.07C0 18.1 4.39 23.09 10.13 24v-8.44H7.08v-3.49h3.04V9.41c0-3.02 1.8-4.7 4.54-4.7 1.31 0 2.68.24 2.68.24v2.97h-1.5c-1.5 0-1.96.93-1.96 1.89v2.26h3.32l-.53 3.49h-2.8V24C19.62 23.09 24 18.1 24 12.07z" />
      </svg>
    ),
    description: 'Custom Audiences API',
  },
  {
    name: 'Instagram',
    logo: (
      <svg className="w-8 h-8" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z" />
      </svg>
    ),
    description: 'Instagram Graph API',
  },
  {
    name: 'WhatsApp',
    logo: (
      <svg className="w-8 h-8" viewBox="0 0 24 24" fill="currentColor">
        <path d="M17.47 14.38c-.3-.15-1.76-.87-2.03-.97-.27-.1-.47-.15-.67.15-.2.3-.77.97-.94 1.17-.17.2-.35.22-.64.07-.3-.15-1.26-.46-2.4-1.48-.88-.79-1.48-1.76-1.65-2.06-.17-.3-.02-.46.13-.61.13-.13.3-.35.44-.52.15-.17.2-.3.3-.5.1-.2.05-.37-.03-.52-.07-.15-.67-1.61-.92-2.2-.24-.58-.48-.5-.67-.51h-.57c-.2 0-.52.07-.8.37-.27.3-1.04 1.02-1.04 2.48s1.07 2.88 1.22 3.08c.15.2 2.1 3.21 5.1 4.5.71.31 1.27.49 1.7.63.72.23 1.37.2 1.88.12.58-.09 1.76-.72 2.01-1.42.25-.7.25-1.29.17-1.42-.07-.13-.27-.2-.57-.35zM12.05 21.79h-.01a9.87 9.87 0 01-5.03-1.38l-.36-.21-3.74.98 1-3.65-.24-.37a9.86 9.86 0 01-1.51-5.26c0-5.45 4.44-9.88 9.9-9.88a9.82 9.82 0 016.99 2.9 9.82 9.82 0 012.9 7c-.01 5.45-4.45 9.87-9.9 9.87zm8.42-18.3A11.8 11.8 0 0012.05 0C5.5 0 .16 5.34.16 11.9c0 2.1.55 4.14 1.59 5.95L.06 24l6.3-1.65a11.87 11.87 0 005.68 1.45h.01c6.55 0 11.89-5.34 11.89-11.9a11.82 11.82 0 00-3.47-8.41z" />
      </svg>
    ),
    description: 'WhatsApp Business API',
  },
];

const features = [
  {
    icon: CloudArrowUpIcon,
    title: 'One-Click Sync',
    description:
      'Push your CDP segments to Facebook, Instagram, and WhatsApp with a single click. No manual exports or uploads required.',
    color: '#f97316',
  },
  {
    icon: ClockIcon,
    title: 'Auto-Refresh',
    description:
      'Keep audiences fresh with configurable sync intervals from 1 hour to 1 week. Set it and forget it.',
    color: '#06b6d4',
  },
  {
    icon: UserGroupIcon,
    title: 'Smart Matching',
    description:
      'Hashed identifier matching for emails, phones, and MAIDs ensures privacy while maximizing match rates.',
    color: '#34c759',
  },
  {
    icon: ChartBarIcon,
    title: 'Match Rate Analytics',
    description:
      'Track match rates, profiles synced, and audience health across Facebook, Instagram, and WhatsApp in one dashboard.',
    color: '#a855f7',
  },
  {
    icon: ShieldCheckIcon,
    title: 'Privacy-First',
    description: 'All PII is hashed before transmission. GDPR and CCPA compliant by design.',
    color: '#3b82f6',
  },
];

export default function AudienceSyncLaunch() {
  return (
    <PageLayout>
      {/* Back Link */}
      <div className="py-6 px-6">
        <div className="max-w-4xl mx-auto">
          <Link
            to="/"
            className="inline-flex items-center gap-2 text-sm transition-colors"
            style={{ color: 'rgba(255, 255, 255, 0.6)' }}
          >
            <ArrowLeftIcon className="w-4 h-4" />
            Back to Home
          </Link>
        </div>
      </div>

      {/* Hero */}
      <section className="py-12 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <div
            className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm mb-6"
            style={{
              background:
                'linear-gradient(135deg, rgba(168, 85, 247, 0.2) 0%, rgba(6, 182, 212, 0.2) 100%)',
              border: '1px solid rgba(168, 85, 247, 0.3)',
            }}
          >
            <span
              className="w-2 h-2 rounded-full animate-pulse"
              style={{ background: '#34c759' }}
            />
            <span style={{ color: '#a855f7' }}>New Feature</span>
          </div>

          <h1
            className="text-4xl md:text-5xl lg:text-6xl font-bold mb-6"
            style={{ fontFamily: "'Inter', sans-serif" }}
          >
            <span className="text-white">Meta Audience</span>
            <br />
            <span
              style={{
                background: 'linear-gradient(135deg, #a855f7 0%, #06b6d4 50%, #f97316 100%)',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
              }}
            >
              Audience Sync
            </span>
          </h1>

          <p
            className="text-lg md:text-xl max-w-2xl mx-auto mb-8"
            style={{ color: 'rgba(255, 255, 255, 0.7)' }}
          >
            Push your CDP segments to Facebook, Instagram, and WhatsApp with one click. Keep your
            audiences fresh with automated syncing and maximize your ad targeting precision.
          </p>

          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Link
              to="/signup"
              className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl font-semibold text-white transition-all hover:scale-105"
              style={{
                background: 'linear-gradient(135deg, #a855f7 0%, #06b6d4 100%)',
                boxShadow: '0 4px 20px rgba(168, 85, 247, 0.4)',
              }}
            >
              Start Free Trial
              <ArrowRightIcon className="w-5 h-5" />
            </Link>
            <Link
              to="/solutions/audience-sync"
              className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl font-semibold transition-all"
              style={{
                background: 'rgba(255, 255, 255, 0.06)',
                border: '1px solid rgba(255, 255, 255, 0.12)',
                color: '#ffffff',
              }}
            >
              Learn More
            </Link>
          </div>
        </div>
      </section>

      {/* Supported Platforms */}
      <section className="py-16 px-6">
        <div className="max-w-5xl mx-auto">
          <h2 className="text-2xl font-bold text-white text-center mb-12">
            Sync to All Major Ad Platforms
          </h2>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
            {platforms.map((platform) => (
              <div
                key={platform.name}
                className="p-6 rounded-2xl text-center transition-all hover:scale-105 group"
                style={{
                  background: 'rgba(255, 255, 255, 0.04)',
                  border: '1px solid rgba(255, 255, 255, 0.08)',
                }}
              >
                <div
                  className="w-16 h-16 rounded-xl mx-auto mb-4 flex items-center justify-center transition-colors"
                  style={{
                    background: 'rgba(255, 255, 255, 0.08)',
                    color: 'rgba(255, 255, 255, 0.7)',
                  }}
                >
                  {platform.logo}
                </div>
                <h3 className="text-lg font-semibold text-white mb-1">{platform.name}</h3>
                <p className="text-sm" style={{ color: 'rgba(255, 255, 255, 0.5)' }}>
                  {platform.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="py-16 px-6">
        <div className="max-w-5xl mx-auto">
          <h2 className="text-2xl font-bold text-white text-center mb-4">Why Audience Sync?</h2>
          <p
            className="text-center max-w-2xl mx-auto mb-12"
            style={{ color: 'rgba(255, 255, 255, 0.6)' }}
          >
            Stop wasting time with manual exports and CSV uploads. Stratum's Audience Sync keeps
            your targeting fresh and your campaigns optimized.
          </p>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {features.map((feature) => (
              <div
                key={feature.title}
                className="p-6 rounded-2xl backdrop-blur-xl transition-all hover:scale-[1.02]"
                style={{
                  background: `${feature.color}15`,
                  border: `1px solid ${feature.color}30`,
                  boxShadow: `0 8px 32px ${feature.color}10`,
                }}
              >
                <feature.icon className="w-10 h-10 mb-4" style={{ color: feature.color }} />
                <h3 className="text-lg font-semibold text-white mb-2">{feature.title}</h3>
                <p className="text-sm" style={{ color: 'rgba(255, 255, 255, 0.6)' }}>
                  {feature.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* How It Works */}
      <section className="py-16 px-6">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-2xl font-bold text-white text-center mb-12">How It Works</h2>

          <div className="space-y-8">
            {[
              {
                step: '1',
                title: 'Connect Your Ad Accounts',
                description:
                  'Connect your Facebook, Instagram, and WhatsApp ad accounts read-only with secure OAuth.',
              },
              {
                step: '2',
                title: 'Select a CDP Segment',
                description:
                  'Choose from your existing segments or create a new one with our powerful segment builder.',
              },
              {
                step: '3',
                title: 'Configure Sync Settings',
                description:
                  'Set your sync frequency, choose identifier types (email, phone, MAID), and enable auto-refresh.',
              },
              {
                step: '4',
                title: 'Launch & Monitor',
                description:
                  'Hit sync and watch your audiences populate across platforms. Track match rates and audience health in real-time.',
              },
            ].map((item) => (
              <div key={item.step} className="flex gap-6 items-start">
                <div
                  className="w-12 h-12 rounded-xl flex items-center justify-center flex-shrink-0"
                  style={{
                    background: 'linear-gradient(135deg, #a855f7 0%, #06b6d4 100%)',
                  }}
                >
                  <span className="text-white font-bold text-lg">{item.step}</span>
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-white mb-2">{item.title}</h3>
                  <p style={{ color: 'rgba(255, 255, 255, 0.6)' }}>{item.description}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <div
            className="p-12 rounded-3xl backdrop-blur-xl"
            style={{
              background:
                'linear-gradient(135deg, rgba(168, 85, 247, 0.15) 0%, rgba(6, 182, 212, 0.15) 100%)',
              border: '1px solid rgba(168, 85, 247, 0.3)',
              boxShadow: '0 8px 32px rgba(168, 85, 247, 0.15), 0 8px 32px rgba(6, 182, 212, 0.15)',
            }}
          >
            <h2 className="text-3xl font-bold text-white mb-4">
              Ready to Supercharge Your Targeting?
            </h2>
            <p className="text-lg mb-8" style={{ color: 'rgba(255, 255, 255, 0.7)' }}>
              Start your 14-day free trial and sync your first audience in minutes.
            </p>
            <div className="flex flex-col sm:flex-row gap-4 justify-center">
              <Link
                to="/signup"
                className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl font-semibold text-white transition-all hover:scale-105"
                style={{
                  background: 'linear-gradient(135deg, #a855f7 0%, #06b6d4 100%)',
                  boxShadow: '0 4px 20px rgba(168, 85, 247, 0.4)',
                }}
              >
                Start Free Trial
                <ArrowRightIcon className="w-5 h-5" />
              </Link>
              <Link
                to="/contact"
                className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl font-semibold transition-all"
                style={{
                  background: 'rgba(255, 255, 255, 0.06)',
                  border: '1px solid rgba(255, 255, 255, 0.12)',
                  color: '#ffffff',
                }}
              >
                Talk to Sales
              </Link>
            </div>
          </div>
        </div>
      </section>
    </PageLayout>
  );
}
