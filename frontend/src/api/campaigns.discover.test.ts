/**
 * Discover campaigns mutation is exported for Campaigns UI wiring.
 */
import { describe, expect, it } from 'vitest';
import { campaignsApi, useDiscoverCampaigns } from '@/api/campaigns';

describe('campaign discovery client', () => {
  it('exposes discoverCampaigns on the API module', () => {
    expect(typeof campaignsApi.discoverCampaigns).toBe('function');
  });

  it('exports useDiscoverCampaigns hook', () => {
    expect(typeof useDiscoverCampaigns).toBe('function');
  });
});
