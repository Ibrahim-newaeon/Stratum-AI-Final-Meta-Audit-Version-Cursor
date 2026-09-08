/**
 * SEO Component
 * Manages document head meta tags for SEO and social sharing
 */

import { Helmet } from 'react-helmet-async';

interface SEOProps {
  title?: string;
  description?: string;
  keywords?: string;
  image?: string;
  url?: string;
  type?: 'website' | 'article' | 'product';
  twitterCard?: 'summary' | 'summary_large_image';
  noIndex?: boolean;
  structuredData?: object;
}

/**
 * Fallback origin for the rare context with no `window` (unit tests, any future
 * prerender). Never used in a browser - see `siteOrigin()`.
 *
 * It is deliberately the host the SPA is actually served from. The previous
 * value, `https://stratum-ai.com`, was a different domain from the deployment,
 * so every canonical link and every og:url pointed somewhere the page does not
 * live - which is what Meta's Sharing Debugger surfaced.
 */
const FALLBACK_ORIGIN = 'https://meta.stratumai.app';

/**
 * The shared social card in `public/og-image.png`, and its real dimensions.
 *
 * 1200x630 is the ratio Facebook and X both crop to without letterboxing.
 * Declaring the size lets the first scrape render the large card immediately
 * instead of a small one until Facebook has fetched the file once.
 */
const OG_IMAGE_PATH = '/og-image.png';
const OG_IMAGE_WIDTH = 1200;
const OG_IMAGE_HEIGHT = 630;

/**
 * Origin to build absolute canonical and social URLs from.
 *
 * Read from the browser at call time rather than hardcoded, so a deployment on
 * any host - production, a preview build, localhost - describes itself
 * correctly instead of advertising somebody else's domain. `VITE_SITE_URL`
 * overrides it for a deployment whose public origin differs from the origin the
 * SPA is served on (behind a proxy or a vanity domain).
 */
function siteOrigin(): string {
  const configured = import.meta.env.VITE_SITE_URL;
  if (typeof configured === 'string' && configured.trim()) {
    return configured.trim().replace(/\/$/, '');
  }
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin;
  }
  return FALLBACK_ORIGIN;
}

/**
 * Absolute URL of the page being rendered, used when a caller passes no `url`.
 *
 * Pages no longer hardcode their own absolute URL: doing so meant every new
 * route had to remember the host, and four of them had the wrong one.
 */
function currentUrl(): string {
  if (typeof window !== 'undefined' && window.location) {
    return `${siteOrigin()}${window.location.pathname}`;
  }
  return siteOrigin();
}

const defaultMeta = {
  siteName: 'Stratum AI',
  title: 'Stratum AI - Revenue Operating System',
  description:
    'Stratum AI is an AI-powered revenue operating system for ad teams. We optimize Facebook, Instagram and WhatsApp campaigns with Trust-Gated Autopilot — every AI decision is auditable, explainable and reversible, with one-click human override.',
  image: OG_IMAGE_PATH,
  keywords:
    'marketing intelligence, CDP, customer data platform, ad optimization, ROAS, attribution, Facebook ads, Instagram ads, WhatsApp campaigns, Meta ads',
};

export function SEO({
  title,
  description = defaultMeta.description,
  keywords = defaultMeta.keywords,
  image = defaultMeta.image,
  url,
  type = 'website',
  twitterCard = 'summary_large_image',
  noIndex = false,
  structuredData,
}: SEOProps) {
  const fullTitle = title ? `${title} | ${defaultMeta.siteName}` : defaultMeta.title;

  // Absolute URLs are required here: og:url, og:image and the canonical link are
  // read by crawlers that have no page context to resolve a relative path against.
  const canonicalUrl = url ?? currentUrl();
  const fullImageUrl = image.startsWith('http') ? image : `${siteOrigin()}${image}`;

  return (
    <Helmet>
      {/* Primary Meta Tags */}
      <title>{fullTitle}</title>
      <meta name="title" content={fullTitle} />
      <meta name="description" content={description} />
      <meta name="keywords" content={keywords} />

      {/* Robots */}
      {noIndex && <meta name="robots" content="noindex, nofollow" />}

      {/* Open Graph / Facebook */}
      <meta property="og:type" content={type} />
      <meta property="og:url" content={canonicalUrl} />
      <meta property="og:title" content={fullTitle} />
      <meta property="og:description" content={description} />
      <meta property="og:image" content={fullImageUrl} />
      {/* Declared so the first scrape renders the large card rather than a
          small one. These match public/og-image.png; a page passing its own
          `image` is responsible for its own dimensions. */}
      <meta property="og:image:width" content={String(OG_IMAGE_WIDTH)} />
      <meta property="og:image:height" content={String(OG_IMAGE_HEIGHT)} />
      <meta property="og:image:alt" content={fullTitle} />
      <meta property="og:site_name" content={defaultMeta.siteName} />

      {/* Twitter Card */}
      <meta name="twitter:card" content={twitterCard} />
      <meta name="twitter:url" content={canonicalUrl} />
      <meta name="twitter:title" content={fullTitle} />
      <meta name="twitter:description" content={description} />
      <meta name="twitter:image" content={fullImageUrl} />

      {/* Canonical URL */}
      <link rel="canonical" href={canonicalUrl} />

      {/* Structured Data / JSON-LD */}
      {structuredData && (
        <script type="application/ld+json">{JSON.stringify(structuredData)}</script>
      )}
    </Helmet>
  );
}

/**
 * Page-specific SEO configurations
 */
export const pageSEO = {
  landing: {
    title: undefined, // Uses default
    description:
      'Transform your marketing with AI-powered intelligence. Trust-Gated Autopilot ensures automations only execute when data is reliable.',
  },
  pricing: {
    title: 'Pricing',
    description:
      'Simple, transparent pricing for Stratum AI. Start with a 14-day free trial. Plans from $499/month for growing teams.',
  },
  features: {
    title: 'Features',
    description:
      'Explore Stratum AI features: Trust Engine, Signal Health monitoring, CDP with audience sync, predictive analytics, and more.',
  },
  faq: {
    title: 'FAQ',
    description:
      'Frequently asked questions about Stratum AI. Learn about pricing, features, integrations, data security, and support.',
  },
  login: {
    title: 'Sign In',
    description:
      'Sign in to your Stratum AI account to access your marketing intelligence dashboard.',
    noIndex: true,
  },
  signup: {
    title: 'Sign Up',
    description:
      'Create your Stratum AI account and start your 14-day free trial. No credit card required.',
  },
  contact: {
    title: 'Contact Us',
    description:
      "Get in touch with the Stratum AI team. We're here to help with sales inquiries, support, and partnerships.",
  },
  about: {
    title: 'About Us',
    description:
      "Learn about Stratum AI's mission to bring trust and transparency to marketing automation.",
  },
  cdp: {
    title: 'Customer Data Platform',
    description:
      'Unify customer profiles, sync audiences to ad platforms, and build smart segments with Stratum AI CDP.',
  },
  docs: {
    title: 'Documentation',
    description:
      'Stratum AI documentation. Learn how to integrate, configure, and get the most out of the platform.',
  },
};

export default SEO;
