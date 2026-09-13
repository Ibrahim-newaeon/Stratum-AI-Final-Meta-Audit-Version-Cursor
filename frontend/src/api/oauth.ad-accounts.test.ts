/**
 * OAuth client exposes Sync Ad Accounts for Meta Connect recovery.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('oauth ad account sync client', () => {
  it('posts to /oauth/{platform}/accounts/sync', () => {
    const source = readFileSync(resolve(__dirname, './oauth.ts'), 'utf8');
    expect(source).toMatch(/syncOAuthAdAccounts/);
    expect(source).toMatch(/\/oauth\/\$\{platform\}\/accounts\/sync/);
  });
});

describe('ConnectPlatforms ad account sync UX', () => {
  it('offers Sync ad accounts after Meta is connected', () => {
    const source = readFileSync(
      resolve(__dirname, '../views/tenant/ConnectPlatforms.tsx'),
      'utf8'
    );
    expect(source).toMatch(/syncOAuthAdAccounts/);
    expect(source).toMatch(/Sync ad accounts/);
    expect(source).not.toMatch(/Ad accounts will appear after sync/);
  });
});
