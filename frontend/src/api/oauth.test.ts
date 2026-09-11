import { describe, expect, it } from 'vitest';
import { FRONTEND_CONNECT_PATH } from './oauth';

describe('OAuth connect path', () => {
  it('is a dashboard route, not the bare /connect 404', () => {
    expect(FRONTEND_CONNECT_PATH).toBe('/dashboard/campaigns/connect');
  });
});
