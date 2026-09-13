/**
 * Automation Rules must not invent mock “active” rules when the API is empty.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('Rules view honesty', () => {
  it('does not ship a mockRules fallback catalogue', () => {
    const source = readFileSync(resolve(__dirname, './Rules.tsx'), 'utf8');
    expect(source).not.toMatch(/\bmockRules\b/);
    expect(source).toMatch(/no mock active rules/i);
  });
});
