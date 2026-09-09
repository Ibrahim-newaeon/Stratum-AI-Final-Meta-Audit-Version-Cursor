/**
 * Tests for the onboarding mutations' cache invalidation.
 *
 * The behaviour under test is narrow but was a real production bug: skipping
 * the wizard showed "You can complete setup later from Settings", then bounced
 * the person straight back to onboarding, and only a second skip reached the
 * dashboard.
 *
 * The cause was ordering, not the backend. `POST /onboarding/skip` correctly
 * marks the record SKIPPED and `GET /onboarding/check` correctly reports
 * `required: false` afterwards - but `onSuccess` fired `invalidateQueries`
 * without returning it, so `mutateAsync` resolved before the refetch. The
 * caller navigated immediately, `OnboardingGuard` mounted, read the *stale*
 * cached check that still said `required: true`, and redirected back.
 *
 * So the assertion is about timing: by the time `mutateAsync` resolves, the
 * check query must already hold the fresh answer.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { apiClient } from './client';
import { onboardingQueryKeys, useOnboardingCheck, useSkipOnboarding } from './onboarding';

vi.mock('./client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
}));

// vi.mocked() keeps axios's overloaded call signatures, which have no mock
// helpers on them, so the two methods are narrowed to plain Mocks here.
const mockedClient = apiClient as unknown as {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
};

function makeWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe('useSkipOnboarding', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('leaves the check query fresh by the time the mutation resolves', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    // The state the guard is holding when someone clicks Skip.
    queryClient.setQueryData(onboardingQueryKeys.check(), {
      required: true,
      current_step: 'business_profile',
    });

    mockedClient.post.mockResolvedValue({
      data: { data: { status: 'skipped', current_step: 'business_profile' } },
    } as never);
    // What /check answers once the record is SKIPPED.
    mockedClient.get.mockResolvedValue({
      data: { data: { required: false, current_step: 'business_profile' } },
    } as never);

    // useOnboardingCheck stands in for OnboardingGuard: an *active* observer,
    // which is what makes an invalidation actually refetch rather than just
    // mark the entry stale.
    const { result } = renderHook(
      () => ({ check: useOnboardingCheck(), skip: useSkipOnboarding() }),
      { wrapper: makeWrapper(queryClient) }
    );
    await waitFor(() => expect(result.current.check.isLoading).toBe(false));

    await result.current.skip.mutateAsync();

    // The bug: this read still said `required: true` here, so the guard
    // redirected back to the wizard the moment the caller navigated.
    expect(queryClient.getQueryData(onboardingQueryKeys.check())).toEqual({
      required: false,
      current_step: 'business_profile',
    });
    expect(mockedClient.post).toHaveBeenCalledWith('/onboarding/skip');
  });

  it('posts to the skip endpoint and nothing else', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    mockedClient.post.mockResolvedValue({
      data: { data: { status: 'skipped', current_step: 'business_profile' } },
    } as never);

    const { result } = renderHook(() => useSkipOnboarding(), {
      wrapper: makeWrapper(queryClient),
    });
    await result.current.mutateAsync();

    expect(mockedClient.post).toHaveBeenCalledTimes(1);
  });
});
