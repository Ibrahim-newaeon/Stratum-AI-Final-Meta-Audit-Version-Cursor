/**
 * WhatsApp Module G credentials client (not CAPI).
 */
import { describe, expect, it } from 'vitest';
import { whatsappCredentialsApi } from '@/api/whatsappCredentials';

describe('whatsappCredentialsApi', () => {
  it('exposes status, upsert, and disconnect', () => {
    expect(typeof whatsappCredentialsApi.getStatus).toBe('function');
    expect(typeof whatsappCredentialsApi.upsert).toBe('function');
    expect(typeof whatsappCredentialsApi.disconnect).toBe('function');
  });
});
