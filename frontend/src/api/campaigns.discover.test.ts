/**
 * Campaign discovery client — thin adapter for POST /campaigns/discover.
 * Kept API-only so the future dashboard redesign can call the same hook.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { apiClient } from './client';
import { campaignsApi } from './campaigns';

vi.mock('./client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

const mockedPost = apiClient.post as unknown as ReturnType<typeof vi.fn>;

describe('campaignsApi.discoverCampaigns', () => {
  beforeEach(() => {
    mockedPost.mockReset();
  });

  it('POSTs /campaigns/discover and returns task payload', async () => {
    mockedPost.mockResolvedValue({
      data: {
        success: true,
        data: { task_id: 'celery-task-1', tenant_id: 42 },
        message: 'Campaign discovery queued successfully',
      },
    });

    const result = await campaignsApi.discoverCampaigns();

    expect(mockedPost).toHaveBeenCalledWith('/campaigns/discover');
    expect(result).toEqual({ task_id: 'celery-task-1', tenant_id: 42 });
  });
});
