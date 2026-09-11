/**
 * Status page must not present sample uptime as live probes.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('Status page honesty', () => {
  it('labels sample status content as illustrative', () => {
    const source = readFileSync(
      resolve(__dirname, './pages/resources/Status.tsx'),
      'utf8'
    );
    expect(source).toMatch(/Illustrative sample/i);
    expect(source).toMatch(/illustrative/i);
    expect(source).not.toMatch(/Real-time status/i);
  });
});
