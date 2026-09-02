/**
 * Tier landing page content configuration.
 *
 * Stratum AI is an AI-powered revenue operating system for ad teams,
 * optimizing Facebook, Instagram and WhatsApp campaigns with
 * Trust-Gated Autopilot. All content here is Meta-only by design.
 */

export type TierId = 'starter' | 'professional' | 'enterprise';

export interface TierMetric {
  value: string;
  label: string;
  description?: string;
}

export interface TierHeroContent {
  tagline: string;
  headline: string;
  highlightedText: string;
  subheadline: string;
  description: string;
  primaryCta: string;
  secondaryCta: string;
  metrics: TierMetric[];
  trustBadges: string[];
}

export interface TierFeature {
  name: string;
  description?: string;
  included: boolean;
  highlight?: boolean;
}

export interface TierFeatureCategory {
  name: string;
  /** Icon key rendered by TierFeatureGrid (shield, users, chart, document, target, sync, funnel, plug, brain, fingerprint, globe, cog, lock, headset) */
  icon: string;
  features: TierFeature[];
}

export interface TierTestimonial {
  quote: string;
  author: string;
  role: string;
  company: string;
  companySize: string;
  metric?: {
    value: string;
    label: string;
  };
}

export interface TierPricing {
  price: string;
  period?: string;
  savings?: string;
  adSpendLimit: string;
  accountLimit: string;
  nextTier?: {
    name: string;
    teaser: string;
    link: string;
  } | null;
}

export interface TierFAQ {
  question: string;
  answer: string;
}

export interface TierVisuals {
  gradientFrom: string;
  gradientTo: string;
  accentColor: string;
  badgeColor: string;
  badgeText?: string;
}

export interface TierContent {
  id: TierId;
  name: string;
  hero: TierHeroContent;
  featureCategories: TierFeatureCategory[];
  testimonials: TierTestimonial[];
  pricing: TierPricing;
  faqs: TierFAQ[];
  visuals: TierVisuals;
}

const SHARED_TRUST_BADGES = [
  'SOC 2 Type II',
  'GDPR & CCPA compliant',
  'Read-only account access',
  'No credit card required',
];

const starter: TierContent = {
  id: 'starter',
  name: 'Starter',
  hero: {
    tagline: 'For growing ad teams',
    headline: 'Put your Meta ads on',
    highlightedText: 'trusted autopilot',
    subheadline: 'AI optimization for Facebook, Instagram and WhatsApp campaigns',
    description:
      'Stratum AI watches your signal health around the clock and only automates when your data can be trusted. Every AI decision is auditable, explainable and reversible - with one-click human override.',
    primaryCta: 'Start 14-day free trial',
    secondaryCta: 'Watch demo',
    metrics: [
      { value: '3', label: 'Ad Accounts', description: 'Facebook, Instagram & WhatsApp' },
      { value: '$25K', label: 'Monthly Spend', description: 'Combined across accounts' },
      { value: '+18%', label: 'Avg ROI Lift', description: 'Typical Starter-tier improvement' },
    ],
    trustBadges: SHARED_TRUST_BADGES,
  },
  featureCategories: [
    {
      name: 'Trust-Gated Autopilot',
      icon: 'shield',
      features: [
        {
          name: 'Signal health monitoring',
          description: 'Continuous 0-100 scoring of your event data reliability.',
          included: true,
          highlight: true,
        },
        {
          name: 'Assisted automation',
          description: 'AI recommends, you approve. Automation holds when signals degrade.',
          included: true,
          highlight: true,
        },
        {
          name: 'One-click human override',
          description: 'Pause or reverse any AI action instantly.',
          included: true,
        },
        {
          name: 'Custom Autopilot Rules',
          description: 'Define your own if/then automation logic.',
          included: false,
        },
      ],
    },
    {
      name: 'Predictive Models',
      icon: 'brain',
      features: [
        {
          name: 'ROAS forecasting',
          description: 'Predict return on ad spend before you scale.',
          included: true,
          highlight: true,
        },
        { name: 'Creative fatigue detection', included: true },
        { name: 'LTV & churn prediction', included: false },
        { name: 'Conversion prediction', included: false },
      ],
    },
    {
      name: 'CDP Essentials',
      icon: 'users',
      features: [
        {
          name: 'Unified customer profiles',
          description: 'Anonymous to known to customer, in one timeline.',
          included: true,
        },
        { name: 'Segment Builder', description: 'Behavioral segments with live preview.', included: true },
        { name: 'Meta Custom Audiences', description: 'Push segments to Meta Ads.', included: false },
        { name: 'RFM Analysis', included: false },
      ],
    },
    {
      name: 'Reporting & Support',
      icon: 'chart',
      features: [
        { name: 'Unified dashboard', included: true },
        { name: 'CSV export', included: true },
        { name: 'Email support', included: true },
        { name: 'Dedicated account manager', included: false },
      ],
    },
  ],
  testimonials: [
    {
      quote:
        'We connected our Facebook account read-only in ten minutes. Within two weeks Stratum caught a broken pixel before it burned our retargeting budget.',
      author: 'Lina Haddad',
      role: 'Growth Lead',
      company: 'Nimbus Apparel',
      companySize: '11-50',
      metric: { value: '+21%', label: 'ROAS lift' },
    },
    {
      quote:
        'The trust gate is the feature I did not know I needed. Automation simply pauses itself when our signals get noisy - no more blind bid changes.',
      author: 'Omar Said',
      role: 'Performance Marketer',
      company: 'Bloom Botanics',
      companySize: '2-10',
      metric: { value: '-32%', label: 'Wasted spend' },
    },
    {
      quote:
        'Instagram and WhatsApp campaigns finally live in one view. The weekly summaries alone are worth the subscription.',
      author: 'Sara Klein',
      role: 'Founder',
      company: 'Kindly Coffee',
      companySize: '2-10',
    },
  ],
  pricing: {
    price: '$149',
    period: '/month',
    savings: 'Save 20% with annual billing',
    adSpendLimit: 'Up to $25K/month ad spend',
    accountLimit: 'Up to 3 ad accounts',
    nextTier: {
      name: 'Professional',
      teaser: 'Need audience sync and full predictive models?',
      link: '/plans/professional',
    },
  },
  faqs: [
    {
      question: 'Do I need a credit card to start the trial?',
      answer:
        'No. Start a 14-day free trial with no credit card. Connect any ad account read-only and see your signal health within minutes.',
    },
    {
      question: 'Can Stratum change my campaigns without my approval?',
      answer:
        'Not on Starter. Automation runs in assisted mode: the AI recommends actions and holds them for your approval. Even on higher tiers, Autopilot only executes when signal health passes the Trust Gate, and every action is reversible.',
    },
    {
      question: 'Which platforms are supported?',
      answer:
        'Stratum is built for Meta: Facebook, Instagram and WhatsApp campaigns. We connect through the official Meta APIs with read-only access by default.',
    },
    {
      question: 'What is the Trust Gate?',
      answer:
        'The Trust Gate is a safety checkpoint that validates your data quality before any automation runs. If your signal health drops below the threshold, Autopilot holds and alerts you instead of acting on unreliable data.',
    },
  ],
  visuals: {
    gradientFrom: 'from-blue-500',
    gradientTo: 'to-cyan-500',
    accentColor: 'text-cyan-400',
    badgeColor: 'bg-cyan-500',
  },
};

const professional: TierContent = {
  id: 'professional',
  name: 'Professional',
  hero: {
    tagline: 'For scaling performance teams',
    headline: 'Turn customer data into',
    highlightedText: 'revenue on autopilot',
    subheadline: 'Full CDP, audience sync and predictive models for Meta advertisers',
    description:
      'Everything in Starter, plus one-click Meta audience sync, RFM segmentation, funnels and the full predictive suite - ROAS, LTV, churn, conversion and creative fatigue. Trust-Gated Autopilot executes only when your signals are healthy.',
    primaryCta: 'Start 14-day free trial',
    secondaryCta: 'Watch demo',
    metrics: [
      { value: '10', label: 'Ad Accounts', description: 'Facebook, Instagram & WhatsApp' },
      { value: '$250K', label: 'Monthly Spend', description: 'Combined across accounts' },
      { value: '+34%', label: 'Avg ROI Lift', description: 'Typical Professional-tier improvement' },
    ],
    trustBadges: SHARED_TRUST_BADGES,
  },
  featureCategories: [
    {
      name: 'Trust-Gated Autopilot',
      icon: 'shield',
      features: [
        { name: 'Signal health monitoring', included: true },
        {
          name: 'Full Autopilot mode',
          description: 'Hands-free execution when the Trust Gate passes.',
          included: true,
          highlight: true,
        },
        { name: 'One-click human override', included: true },
        {
          name: 'Trust Gate Audit Logs',
          description: 'Complete history of why automations ran or were blocked.',
          included: true,
          highlight: true,
        },
      ],
    },
    {
      name: 'Predictive Models',
      icon: 'brain',
      features: [
        { name: 'ROAS forecasting', included: true },
        {
          name: 'LTV & churn prediction',
          description: 'Know which customers to keep before they leave.',
          included: true,
          highlight: true,
        },
        { name: 'Conversion prediction', included: true },
        { name: 'Creative fatigue detection', included: true },
      ],
    },
    {
      name: 'CDP & Audience Sync',
      icon: 'sync',
      features: [
        {
          name: 'Meta Custom Audiences',
          description: 'Push CDP segments to Meta Ads with hashed identifiers.',
          included: true,
          highlight: true,
        },
        {
          name: 'WhatsApp Audiences',
          description: 'Sync customer lists to WhatsApp campaigns.',
          included: true,
          highlight: true,
        },
        { name: 'RFM Analysis', description: 'Built-in recency/frequency/monetary scoring.', included: true },
        { name: 'Funnel Builder', included: true },
        { name: 'Identity Graph', description: 'Visual cross-device identity resolution.', included: true },
      ],
    },
    {
      name: 'Reporting & Support',
      icon: 'chart',
      features: [
        { name: 'Custom report builder', included: true },
        { name: 'Scheduled reports', included: true },
        { name: 'Priority support', included: true },
        { name: 'Dedicated account manager', included: false },
      ],
    },
  ],
  testimonials: [
    {
      quote:
        'Audience sync changed how we work. We build a segment in the CDP and it is live in Meta Ads minutes later, hashed and matched. Match rates went straight up.',
      author: 'Daniel Mora',
      role: 'Head of Growth',
      company: 'Voltaway',
      companySize: '51-200',
      metric: { value: '+38%', label: 'Match rate' },
    },
    {
      quote:
        'Churn prediction pays for the whole platform. We retarget likely churners on Instagram before they lapse, automatically, and only when the data is trustworthy.',
      author: 'Aisha Rahman',
      role: 'VP Marketing',
      company: 'Fitloop',
      companySize: '51-200',
      metric: { value: '-24%', label: 'Churn rate' },
    },
    {
      quote:
        'The audit log means I can answer "why did the budget change?" in seconds. Our CFO loves that every AI action is explainable.',
      author: 'Tom Becker',
      role: 'Performance Director',
      company: 'Northbeam Travel',
      companySize: '201-500',
      metric: { value: '+31%', label: 'ROAS lift' },
    },
  ],
  pricing: {
    price: '$499',
    period: '/month',
    savings: 'Save 20% with annual billing',
    adSpendLimit: 'Up to $250K/month ad spend',
    accountLimit: 'Up to 10 ad accounts',
    nextTier: {
      name: 'Enterprise',
      teaser: 'Need unlimited scale, SSO and a dedicated team?',
      link: '/plans/enterprise',
    },
  },
  faqs: [
    {
      question: 'How does Meta audience sync work?',
      answer:
        'You build a segment in the CDP, and Stratum pushes it to Meta Custom Audiences using hashed identifiers (email, phone, mobile advertiser ID). Auto-sync keeps audiences fresh on a schedule you choose, and match rates are tracked per audience.',
    },
    {
      question: 'What happens when signal health drops?',
      answer:
        'The Trust Gate holds all automation and alerts you. Below the healthy threshold (70), Autopilot never executes - it switches to alert-only mode until your data quality recovers. This is a hard rule, not a preference.',
    },
    {
      question: 'Which predictive models are included?',
      answer:
        'Professional includes the full suite: ROAS forecasting, LTV, churn, conversion prediction and creative fatigue detection - all trained on your own Facebook, Instagram and WhatsApp performance data.',
    },
    {
      question: 'Is my customer data safe?',
      answer:
        'Yes. Stratum is SOC 2 Type II certified and GDPR/CCPA compliant. PII is hashed before any platform sync, consent is managed per data type, and every data operation is audit-logged.',
    },
  ],
  visuals: {
    gradientFrom: 'from-purple-500',
    gradientTo: 'to-pink-500',
    accentColor: 'text-purple-400',
    badgeColor: 'bg-purple-500',
    badgeText: 'Most Popular',
  },
};

const enterprise: TierContent = {
  id: 'enterprise',
  name: 'Enterprise',
  hero: {
    tagline: 'For brands and agencies at scale',
    headline: 'A revenue operating system',
    highlightedText: 'your auditors will love',
    subheadline: 'Unlimited scale, governance and white-glove support for Meta advertising',
    description:
      'Everything in Professional, plus unlimited accounts and spend, custom Autopilot rules, SSO and role-based access, API access and a dedicated account team. Every AI decision stays auditable, explainable and reversible.',
    primaryCta: 'Contact Sales',
    secondaryCta: 'Watch demo',
    metrics: [
      { value: 'Unlimited', label: 'Ad Accounts', description: 'Facebook, Instagram & WhatsApp' },
      { value: 'Custom', label: 'Monthly Spend', description: 'No platform-imposed ceiling' },
      { value: '+42%', label: 'Avg ROI Lift', description: 'Typical Enterprise-tier improvement' },
    ],
    trustBadges: SHARED_TRUST_BADGES,
  },
  featureCategories: [
    {
      name: 'Governance & Control',
      icon: 'lock',
      features: [
        {
          name: 'Custom Autopilot Rules',
          description: 'Your own if/then automation logic, gated by trust thresholds.',
          included: true,
          highlight: true,
        },
        { name: 'SSO & role-based access', included: true, highlight: true },
        { name: 'Trust Gate Audit Logs', included: true },
        { name: 'Approval workflows', description: 'Budget changes above your limit always require sign-off.', included: true },
      ],
    },
    {
      name: 'Scale & Integration',
      icon: 'plug',
      features: [
        {
          name: 'API Access',
          description: 'REST API for pulling Stratum data into your own systems.',
          included: true,
          highlight: true,
        },
        { name: 'Unlimited ad accounts', included: true },
        { name: 'Embed widgets & white label', included: true },
        { name: 'Custom data retention', included: true },
      ],
    },
    {
      name: 'CDP & Predictive Suite',
      icon: 'brain',
      features: [
        { name: 'Full CDP with audience sync', included: true },
        { name: 'All predictive models', description: 'ROAS, LTV, churn, conversion, creative fatigue.', included: true },
        { name: 'Predictive Churn Modeling', included: true, highlight: true },
        { name: 'Identity Graph', included: true },
      ],
    },
    {
      name: 'Support',
      icon: 'headset',
      features: [
        { name: 'Dedicated account manager', included: true, highlight: true },
        { name: 'Onboarding & migration service', included: true },
        { name: '99.9% uptime SLA', included: true },
        { name: '24/7 priority support', included: true },
      ],
    },
  ],
  testimonials: [
    {
      quote:
        'We manage forty Meta ad accounts across brands. Custom Autopilot rules with hard trust thresholds gave us automation our compliance team could actually approve.',
      author: 'Ingrid Vos',
      role: 'Director of Media',
      company: 'Atlas Commerce Group',
      companySize: '1000+',
      metric: { value: '40+', label: 'Accounts managed' },
    },
    {
      quote:
        'The API lets us pipe Stratum predictions into our own BI stack. One source of truth for Facebook, Instagram and WhatsApp performance.',
      author: 'Marcus Yuen',
      role: 'Head of Marketing Analytics',
      company: 'Helio Financial',
      companySize: '501-1000',
      metric: { value: '+42%', label: 'ROAS lift' },
    },
    {
      quote:
        'Onboarding was genuinely white-glove. Read-only connection first, a two-week shadow period, then staged Autopilot rollout. Zero surprises.',
      author: 'Priya Nair',
      role: 'CMO',
      company: 'Verdant Retail',
      companySize: '1000+',
    },
  ],
  pricing: {
    price: 'Custom',
    adSpendLimit: 'Unlimited ad spend',
    accountLimit: 'Unlimited ad accounts',
    nextTier: null,
  },
  faqs: [
    {
      question: 'How does Enterprise pricing work?',
      answer:
        'Pricing is tailored to your ad spend, number of accounts and support needs. Contact sales for a quote - most teams are live within two weeks, starting with a read-only connection.',
    },
    {
      question: 'Can we enforce our own automation guardrails?',
      answer:
        'Yes. Custom Autopilot Rules let you define your own if/then logic, spending caps and approval thresholds. All rules are still gated by signal health - automation never executes below the trust threshold.',
    },
    {
      question: 'Do you support SSO and granular permissions?',
      answer:
        'Enterprise includes SAML/OIDC SSO, role-based access control and per-workspace permissions so media buyers, analysts and admins each see exactly what they should.',
    },
    {
      question: 'What compliance certifications do you hold?',
      answer:
        'Stratum AI is SOC 2 Type II certified and GDPR/CCPA compliant, with hashed PII for all platform syncs, per-data-type consent management and full audit logging. DPAs are available on request.',
    },
  ],
  visuals: {
    gradientFrom: 'from-amber-500',
    gradientTo: 'to-orange-600',
    accentColor: 'text-amber-400',
    badgeColor: 'bg-amber-500',
    badgeText: 'White Glove',
  },
};

export const tierLandingContent: Record<TierId, TierContent> = {
  starter,
  professional,
  enterprise,
};

export function isValidTier(tier: string): tier is TierId {
  return tier === 'starter' || tier === 'professional' || tier === 'enterprise';
}

export function getTierContent(tier: string): TierContent | null {
  if (!isValidTier(tier)) return null;
  return tierLandingContent[tier];
}
