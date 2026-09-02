/**
 * AI Testimonials Section - Social Proof
 * 2026 Design: Floating cards with gradient borders
 */

import { motion, useInView } from 'framer-motion';
import { useRef } from 'react';
import { StarIcon } from '@heroicons/react/24/solid';

const testimonials = [
  {
    quote:
      "Trust-Gated Autopilot is a game-changer. We've eliminated bad automation decisions completely. Our team finally sleeps at night knowing the AI won't act on degraded data.",
    author: 'Sarah Chen',
    role: 'VP of Growth',
    company: 'TechScale Inc.',
    avatar: 'SC',
    metric: '+47% ROAS',
    color: 'from-purple-500 to-violet-500',
  },
  {
    quote:
      'The creative fatigue prediction alone saved us $200K in wasted ad spend last quarter. We refresh creatives before they burn out, not after.',
    author: 'Marcus Johnson',
    role: 'Performance Marketing Lead',
    company: 'E-Commerce Giant',
    avatar: 'MJ',
    metric: '-34% CPA',
    color: 'from-cyan-500 to-blue-500',
  },
  {
    quote:
      "Finally, a CDP that doesn't just collect data but actually tells you what to do with it. The LTV predictions are scary accurate—we now focus on the right customers.",
    author: 'Emily Rodriguez',
    role: 'Director of CRM',
    company: 'Subscription Box Co.',
    avatar: 'ER',
    metric: '+62% LTV',
    color: 'from-orange-500 to-amber-500',
  },
  {
    quote:
      'We replaced Segment + Looker + a custom ML pipeline with Stratum. One platform, 6 AI models built-in. Implementation took 2 days.',
    author: 'David Kim',
    role: 'CTO',
    company: 'Growth Startup',
    avatar: 'DK',
    metric: '10x faster',
    color: 'from-green-500 to-emerald-500',
  },
];

const platforms = [
  {
    name: 'Meta',
    api: 'Custom Audiences API',
    icon: (
      <svg className="w-8 h-8" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 2.04c-5.5 0-10 4.49-10 10.02 0 5 3.66 9.15 8.44 9.9v-7H7.9v-2.9h2.54V9.85c0-2.51 1.49-3.89 3.78-3.89 1.09 0 2.23.19 2.23.19v2.47h-1.26c-1.24 0-1.63.77-1.63 1.56v1.88h2.78l-.45 2.9h-2.33v7a10 10 0 0 0 8.44-9.9c0-5.53-4.5-10.02-10-10.02Z" />
      </svg>
    ),
  },
  {
    name: 'Facebook',
    api: 'Marketing API',
    icon: (
      <svg className="w-8 h-8" viewBox="0 0 24 24" fill="currentColor">
        <path d="M24 12.07C24 5.4 18.63 0 12 0S0 5.4 0 12.07C0 18.1 4.39 23.09 10.13 24v-8.44H7.08v-3.49h3.04V9.41c0-3.02 1.8-4.7 4.54-4.7 1.31 0 2.68.24 2.68.24v2.97h-1.5c-1.5 0-1.96.93-1.96 1.89v2.26h3.32l-.53 3.49h-2.8V24C19.62 23.09 24 18.1 24 12.07z" />
      </svg>
    ),
  },
  {
    name: 'Instagram',
    api: 'Instagram Graph API',
    icon: (
      <svg className="w-8 h-8" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z" />
      </svg>
    ),
  },
  {
    name: 'WhatsApp',
    api: 'WhatsApp Business API',
    icon: (
      <svg className="w-8 h-8" viewBox="0 0 24 24" fill="currentColor">
        <path d="M17.47 14.38c-.3-.15-1.76-.87-2.03-.97-.27-.1-.47-.15-.67.15-.2.3-.77.97-.94 1.17-.17.2-.35.22-.64.07-.3-.15-1.26-.46-2.4-1.48-.88-.79-1.48-1.76-1.65-2.06-.17-.3-.02-.46.13-.61.13-.13.3-.35.44-.52.15-.17.2-.3.3-.5.1-.2.05-.37-.03-.52-.07-.15-.67-1.61-.92-2.2-.24-.58-.48-.5-.67-.51h-.57c-.2 0-.52.07-.8.37-.27.3-1.04 1.02-1.04 2.48s1.07 2.88 1.22 3.08c.15.2 2.1 3.21 5.1 4.5.71.31 1.27.49 1.7.63.72.23 1.37.2 1.88.12.58-.09 1.76-.72 2.01-1.42.25-.7.25-1.29.17-1.42-.07-.13-.27-.2-.57-.35zM12.05 21.79h-.01a9.87 9.87 0 0 1-5.03-1.38l-.36-.21-3.74.98 1-3.65-.24-.37a9.86 9.86 0 0 1-1.51-5.26c0-5.45 4.44-9.88 9.9-9.88a9.82 9.82 0 0 1 6.99 2.9 9.82 9.82 0 0 1 2.9 7c-.01 5.45-4.45 9.87-9.9 9.87zm8.42-18.3A11.8 11.8 0 0 0 12.05 0C5.5 0 .16 5.34.16 11.9c0 2.1.55 4.14 1.59 5.95L.06 24l6.3-1.65a11.87 11.87 0 0 0 5.68 1.45h.01c6.55 0 11.89-5.34 11.89-11.9a11.82 11.82 0 0 0-3.47-8.41z" />
      </svg>
    ),
  },
];

export default function AITestimonials() {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: '-100px' });

  return (
    <section className="relative py-32 overflow-hidden" style={{ background: '#000000' }}>
      {/* Ambient orb */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] rounded-full blur-3xl" style={{ background: 'radial-gradient(circle, rgba(0, 199, 190, 0.05), transparent 60%)' }} />

      <div className="relative z-10 max-w-7xl mx-auto px-6" ref={ref}>
        {/* Section Header - Centered */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={isInView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.6 }}
          className="text-center mb-16"
        >
          <h2 className="text-4xl sm:text-5xl font-bold tracking-tight mb-6 text-center">
            <span className="text-white">Trusted by</span>{' '}
            <span style={{ color: '#00c7be' }}>
              Revenue Teams
            </span>
          </h2>

          <p className="text-lg max-w-2xl mx-auto text-center" style={{ color: 'rgba(255, 255, 255, 0.5)' }}>
            See what growth and marketing leaders say about switching to AI-powered revenue
            operations.
          </p>
        </motion.div>

        {/* Testimonials Grid */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={isInView ? { opacity: 1 } : {}}
          transition={{ delay: 0.2, duration: 0.6 }}
          className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-16"
        >
          {testimonials.map((testimonial, index) => (
            <motion.div
              key={testimonial.author}
              initial={{ opacity: 0, y: 20 }}
              animate={isInView ? { opacity: 1, y: 0 } : {}}
              transition={{ delay: 0.1 * index, duration: 0.6 }}
              className="group relative"
            >
              {/* Gradient border effect */}
              <div
                className={`absolute -inset-[1px] rounded-3xl bg-gradient-to-r ${testimonial.color} opacity-0 group-hover:opacity-20 blur-sm transition-opacity duration-500`}
              />

              <div className="relative p-8 rounded-3xl bg-white/[0.02] border border-white/[0.05] hover:border-white/10 transition-all">
                {/* Stars */}
                <div className="flex gap-1 mb-4">
                  {[...Array(5)].map((_, i) => (
                    <StarIcon key={i} className="w-4 h-4 text-yellow-500" />
                  ))}
                </div>

                {/* Quote */}
                <blockquote className="text-gray-300 text-lg leading-relaxed mb-6">
                  "{testimonial.quote}"
                </blockquote>

                {/* Author */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-4">
                    <div
                      className={`w-12 h-12 rounded-full bg-gradient-to-br ${testimonial.color} flex items-center justify-center text-white font-semibold`}
                    >
                      {testimonial.avatar}
                    </div>
                    <div>
                      <div className="font-medium text-white">{testimonial.author}</div>
                      <div className="text-sm text-gray-500">
                        {testimonial.role}, {testimonial.company}
                      </div>
                    </div>
                  </div>

                  {/* Metric Badge */}
                  <div
                    className={`px-4 py-2 rounded-full bg-gradient-to-r ${testimonial.color} bg-opacity-10`}
                  >
                    <span
                      className={`text-sm font-bold bg-gradient-to-r ${testimonial.color} bg-clip-text text-transparent`}
                    >
                      {testimonial.metric}
                    </span>
                  </div>
                </div>
              </div>
            </motion.div>
          ))}
        </motion.div>

        {/* Integration Logos */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={isInView ? { opacity: 1, y: 0 } : {}}
          transition={{ delay: 0.4, duration: 0.6 }}
          className="text-center"
        >
          <p className="text-sm text-gray-500 mb-8">
            Integrates with the platforms you already use
          </p>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 max-w-3xl mx-auto">
            {platforms.map((platform) => (
              <div
                key={platform.name}
                className="flex flex-col items-center justify-center p-6 rounded-2xl bg-white/[0.02] border border-white/[0.05] hover:border-white/10 transition-all"
              >
                <div className="w-12 h-12 rounded-xl flex items-center justify-center mb-3 text-gray-400">
                  {platform.icon}
                </div>
                <span className="text-sm font-medium text-white mb-1">{platform.name}</span>
                <span className="text-xs text-gray-500 text-center">{platform.api}</span>
              </div>
            ))}
          </div>
        </motion.div>

        {/* Stats Bar */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={isInView ? { opacity: 1, y: 0 } : {}}
          transition={{ delay: 0.6, duration: 0.6 }}
          className="mt-16 grid grid-cols-2 md:grid-cols-4 gap-6"
        >
          {[
            { value: '500+', label: 'Companies' },
            { value: '$2.4B+', label: 'Revenue Optimized' },
            { value: '99.7%', label: 'Uptime SLA' },
            { value: '4.9/5', label: 'Customer Rating' },
          ].map((stat) => (
            <div key={stat.label} className="text-center">
              <div className="text-3xl font-bold bg-gradient-to-r from-white to-gray-400 bg-clip-text text-transparent mb-1">
                {stat.value}
              </div>
              <div className="text-sm text-gray-500">{stat.label}</div>
            </div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
