import { describe, expect, it } from 'vitest';
import { campaignBuilderPath } from './campaignBuilder';

describe('campaignBuilderPath', () => {
  it('prefixes tenant routes with /campaign-builder', () => {
    expect(campaignBuilderPath(2, '/ad-accounts/meta')).toBe(
      '/campaign-builder/tenant/2/ad-accounts/meta'
    );
  });

  it('accepts suffixes without a leading slash', () => {
    expect(campaignBuilderPath(7, 'campaign-drafts')).toBe(
      '/campaign-builder/tenant/7/campaign-drafts'
    );
  });
});
