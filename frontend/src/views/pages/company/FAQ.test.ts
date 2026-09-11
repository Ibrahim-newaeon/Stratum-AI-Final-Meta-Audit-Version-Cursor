import { describe, expect, it } from 'vitest';
import { fallbackFaqs } from '@/views/pages/company/FAQ';

describe('FAQ portal copy', () => {
  it('documents Signal Health bands as >= 70 PASS and 40-69 HOLD', () => {
    const signalHealth = fallbackFaqs.find((faq) => faq.id === '7');
    expect(signalHealth?.answer).toContain('at or above 70');
    expect(signalHealth?.answer).toContain('40-69');
    expect(signalHealth?.answer).toContain('below 40');
    expect(signalHealth?.answer).not.toContain('40-70');
  });

  it('points ad-account connection at Connect Platforms, not Tenant Settings', () => {
    const connect = fallbackFaqs.find((faq) => faq.id === '10');
    expect(connect?.answer).toMatch(/Connect Platforms/);
    expect(connect?.answer).toMatch(/Connect Meta Ads/);
    expect(connect?.answer).not.toContain('Tenant Settings');
  });

  it('does not claim Autopilot writes are on in this portal release', () => {
    const autopilot = fallbackFaqs.find((faq) => faq.id === '4');
    expect(autopilot?.answer).toMatch(/writes off/i);
  });

  it('describes this portal as a free account with no payment gateway', () => {
    const pricing = fallbackFaqs.find((faq) => faq.id === '1');
    expect(pricing?.answer).toMatch(/free/i);
    expect(pricing?.answer).toMatch(/no payment gateway/i);
    expect(pricing?.answer).not.toContain('$499');
    const trial = fallbackFaqs.find((faq) => faq.id === '2');
    expect(trial?.answer).toMatch(/no credit card/i);
    expect(trial?.answer).not.toContain('14-day');
  });
});
