/**
 * Dashboard "Sync Campaigns" deep-links to /dashboard/campaigns?discover=1.
 * Guard the wiring so a future rename does not revive the /campaigns/sync 404.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('Campaigns discover/create query deep links', () => {
  const source = readFileSync(resolve(__dirname, './Campaigns.tsx'), 'utf8');

  it('reads discover and create search params from the dashboard quick actions', () => {
    expect(source).toContain("searchParams.get('discover')");
    expect(source).toContain("searchParams.get('create')");
    expect(source).toContain("discover === '1'");
    expect(source).toContain("create === '1'");
    expect(source).toContain('discoverCampaigns.mutate');
    expect(source).toContain('setCreateModalOpen(true)');
  });
});
