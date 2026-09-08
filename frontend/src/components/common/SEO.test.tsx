/**
 * Tests for the SEO component's URL handling.
 *
 * The bug these guard against was invisible in the app and only showed up in
 * Meta's Sharing Debugger: every canonical link, `og:url` and `og:image`
 * pointed at `https://stratum-ai.com`, a different domain from the one the SPA
 * is served on. Four pages additionally hardcoded their own absolute URL, so
 * the wrong host was repeated in five places.
 *
 * The fix derives the origin from the browser, so these assert that behaviour
 * rather than a new hardcoded string.
 */

import { HelmetProvider } from 'react-helmet-async';
import { render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { SEO } from './SEO';

/** Render inside the provider react-helmet-async requires. */
function renderSEO(props: Parameters<typeof SEO>[0] = {}) {
  return render(
    <HelmetProvider>
      <SEO {...props} />
    </HelmetProvider>
  );
}

/** Read one rendered meta tag's content out of the document head. */
function meta(selector: string): string | null {
  return document.head.querySelector(selector)?.getAttribute('content') ?? null;
}

beforeEach(() => {
  document.head.querySelectorAll('meta, link[rel="canonical"]').forEach((el) => el.remove());
});

describe('SEO URLs', () => {
  it('derives og:url and canonical from the current origin and path', async () => {
    window.history.pushState({}, '', '/login');
    renderSEO();

    await waitFor(() => {
      expect(meta('meta[property="og:url"]')).toBe(`${window.location.origin}/login`);
    });
    expect(document.head.querySelector('link[rel="canonical"]')?.getAttribute('href')).toBe(
      `${window.location.origin}/login`
    );
  });

  it('resolves a relative og:image against the current origin', async () => {
    renderSEO();

    await waitFor(() => {
      expect(meta('meta[property="og:image"]')).toBe(`${window.location.origin}/og-image.png`);
    });
  });

  it('leaves an absolute image URL alone', async () => {
    renderSEO({ image: 'https://cdn.example/card.png' });

    await waitFor(() => {
      expect(meta('meta[property="og:image"]')).toBe('https://cdn.example/card.png');
    });
  });

  it('honours an explicit url prop', async () => {
    renderSEO({ url: 'https://example.test/custom' });

    await waitFor(() => {
      expect(meta('meta[property="og:url"]')).toBe('https://example.test/custom');
    });
  });

  it('never advertises the stale marketing domain', async () => {
    window.history.pushState({}, '', '/signup');
    renderSEO();

    await waitFor(() => {
      expect(meta('meta[property="og:url"]')).toBeTruthy();
    });
    // The whole point: no rendered tag may name a host the SPA is not served on.
    expect(document.head.innerHTML).not.toContain('stratum-ai.com');
  });

  it('keeps twitter:url in step with og:url', async () => {
    window.history.pushState({}, '', '/pricing');
    renderSEO();

    await waitFor(() => {
      expect(meta('meta[name="twitter:url"]')).toBe(meta('meta[property="og:url"]'));
    });
  });
});
