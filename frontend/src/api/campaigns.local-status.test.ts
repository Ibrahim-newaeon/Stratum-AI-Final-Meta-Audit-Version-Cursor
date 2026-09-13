/**
 * Campaign pause/activate client calls the local-status API routes.
 * Those routes must not be presented as Meta Ads Manager writes.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('campaigns API local pause/activate', () => {
  it('documents pause and activate as local Stratum status changes', () => {
    const source = readFileSync(resolve(__dirname, './campaigns.ts'), 'utf8');
    expect(source).toMatch(/\/campaigns\/\$\{id\}\/pause/);
    expect(source).toMatch(/\/campaigns\/\$\{id\}\/activate/);
    expect(source).toMatch(/does not write to Meta Ads Manager/i);
  });
});
