import { describe, expect, it } from 'vitest';
import { unwrapCdpBody } from '@/api/cdp';

describe('unwrapCdpBody', () => {
  it('returns raw CDP list bodies that are not APIResponse-wrapped', () => {
    const body = {
      segments: [{ id: 'seg-1', name: 'High value' }],
      total: 1,
    };
    expect(unwrapCdpBody(body)).toEqual(body);
  });

  it('returns raw CDP create bodies without a data envelope', () => {
    const body = {
      id: 'seg-2',
      name: 'New customers',
      status: 'draft',
      rules: { logic: 'and', conditions: [] },
    };
    expect(unwrapCdpBody(body)).toEqual(body);
  });

  it('unwraps APIResponse envelopes when present', () => {
    const segment = { id: 'seg-3', name: 'Wrapped' };
    expect(unwrapCdpBody({ success: true, data: segment })).toEqual(segment);
  });

  it('does not treat missing nested data as an envelope', () => {
    const body = { success: false, data: undefined, message: 'nope' };
    expect(unwrapCdpBody(body)).toEqual(body);
  });
});
