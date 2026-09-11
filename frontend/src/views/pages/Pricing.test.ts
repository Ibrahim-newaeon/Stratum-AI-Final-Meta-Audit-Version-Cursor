import { describe, expect, it } from 'vitest';
import { portalPricingPlan } from '@/views/pages/Pricing';

describe('portal pricing', () => {
  it('offers a single free plan with no checkout', () => {
    expect(portalPricingPlan.price).toBe('$0');
    expect(portalPricingPlan.href).toBe('/signup');
    expect(portalPricingPlan.cta).toMatch(/free/i);
    expect(portalPricingPlan.features.join(' ')).toMatch(/no payment gateway/i);
  });
});
