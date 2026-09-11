/**
 * Connect Platforms — Meta Ads OAuth.
 *
 * Facebook, Instagram, and WhatsApp share one Meta Business connection.
 */

import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowPathIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  LinkIcon,
  XCircleIcon,
} from '@heroicons/react/24/outline';
import { cn } from '@/lib/utils';
import {
  disconnectOAuth,
  getOAuthStatus,
  refreshOAuth,
  startOAuth,
  type OAuthConnectionStatus,
} from '@/api/oauth';

type UiStatus = 'connected' | 'disconnected' | 'expired' | 'error';

const statusConfig: Record<
  UiStatus,
  {
    icon: typeof CheckCircleIcon;
    color: string;
    label: string;
  }
> = {
  connected: {
    icon: CheckCircleIcon,
    color: 'text-emerald-600 dark:text-emerald-400',
    label: 'Connected',
  },
  disconnected: {
    icon: LinkIcon,
    color: 'text-muted-foreground',
    label: 'Not connected',
  },
  expired: {
    icon: ExclamationTriangleIcon,
    color: 'text-amber-600 dark:text-amber-400',
    label: 'Token expired',
  },
  error: {
    icon: XCircleIcon,
    color: 'text-red-600 dark:text-red-400',
    label: 'Connection error',
  },
};

function asUiStatus(status: string | undefined): UiStatus {
  if (status === 'connected' || status === 'expired' || status === 'error') {
    return status;
  }
  return 'disconnected';
}

function formatDay(value?: string | null): string {
  if (!value) return '—';
  const day = value.split('T')[0];
  return day || '—';
}

function errorMessage(error: unknown, fallback: string): string {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data
      ?.detail;
    if (typeof detail === 'string' && detail.trim()) {
      if (detail === 'Email verification required') {
        return 'Verify your email before connecting Meta Ads. Check your inbox, or set SMTP so the verification email can be sent.';
      }
      return detail;
    }
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

export default function ConnectPlatforms() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [banner, setBanner] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);

  const statusQuery = useQuery({
    queryKey: ['oauth-status', 'meta'],
    queryFn: () => getOAuthStatus('meta'),
  });

  useEffect(() => {
    const status = searchParams.get('status');
    const error = searchParams.get('error');
    const message = searchParams.get('message');
    if (!status && !error) {
      return;
    }
    if (status === 'success') {
      setBanner({ kind: 'success', text: 'Meta Ads connected. Ad accounts will appear after sync.' });
      void queryClient.invalidateQueries({ queryKey: ['oauth-status', 'meta'] });
    } else {
      setBanner({
        kind: 'error',
        text: message || error || 'Meta authorization did not complete.',
      });
    }
    setSearchParams({}, { replace: true });
  }, [queryClient, searchParams, setSearchParams]);

  const connectMutation = useMutation({
    mutationFn: () => startOAuth('meta'),
    onSuccess: (result) => {
      if (result.authorization_url) {
        window.location.href = result.authorization_url;
        return;
      }
      setBanner({ kind: 'error', text: 'Meta did not return an authorization URL.' });
    },
    onError: (error) => {
      setBanner({
        kind: 'error',
        text: errorMessage(error, 'Could not start Meta authorization. Check that META_APP_ID is set.'),
      });
    },
  });

  const refreshMutation = useMutation({
    mutationFn: () => refreshOAuth('meta'),
    onSuccess: () => {
      setBanner({ kind: 'success', text: 'Meta token refreshed.' });
      void queryClient.invalidateQueries({ queryKey: ['oauth-status', 'meta'] });
    },
    onError: (error) => {
      setBanner({ kind: 'error', text: errorMessage(error, 'Could not refresh the Meta token.') });
    },
  });

  const disconnectMutation = useMutation({
    mutationFn: () => disconnectOAuth('meta'),
    onSuccess: () => {
      setBanner({ kind: 'success', text: 'Meta Ads disconnected.' });
      void queryClient.invalidateQueries({ queryKey: ['oauth-status', 'meta'] });
    },
    onError: (error) => {
      setBanner({ kind: 'error', text: errorMessage(error, 'Could not disconnect Meta Ads.') });
    },
  });

  const meta: OAuthConnectionStatus | undefined = statusQuery.data;
  const uiStatus = asUiStatus(meta?.status);
  const StatusIcon = statusConfig[uiStatus].icon;
  const busy = connectMutation.isPending || refreshMutation.isPending || disconnectMutation.isPending;

  const connectedOn = useMemo(() => formatDay(meta?.connected_at), [meta?.connected_at]);
  const expiresOn = useMemo(() => formatDay(meta?.token_expires_at), [meta?.token_expires_at]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Connect Platforms</h1>
        <p className="text-muted-foreground">
          Connect your Meta Business account so Stratum can read Facebook, Instagram, and WhatsApp
          ads. This is not Conversion API (CAPI) setup.
        </p>
        <p className="text-xs text-muted-foreground mt-2 flex items-center gap-1">
          <ExclamationTriangleIcon className="w-3 h-3" />
          <span>
            For server-side conversion tracking, go to{' '}
            <Link to="/dashboard/capi-setup" className="text-primary hover:underline">
              CAPI Setup
            </Link>
            .
          </span>
        </p>
      </div>

      {banner && (
        <div
          className={cn(
            'rounded-lg border px-4 py-3 text-sm',
            banner.kind === 'success'
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
              : 'border-red-500/30 bg-red-500/10 text-red-400'
          )}
        >
          {banner.text}
        </div>
      )}

      {statusQuery.isError && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
          {errorMessage(statusQuery.error, 'Could not load connection status.')}
        </div>
      )}

      <div className="rounded-xl border bg-card p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className="h-12 w-12 rounded-lg bg-[#0866FF]/15 flex items-center justify-center">
              <span className="text-lg font-bold text-[#0866FF]">M</span>
            </div>
            <div>
              <h3 className="font-semibold">Meta Ads</h3>
              <p className="text-sm text-muted-foreground">Facebook, Instagram, and WhatsApp</p>
              <div className={cn('mt-1 flex items-center gap-1 text-sm', statusConfig[uiStatus].color)}>
                {statusQuery.isLoading ? (
                  <ArrowPathIcon className="h-4 w-4 animate-spin" />
                ) : (
                  <StatusIcon className="h-4 w-4" />
                )}
                <span>{statusQuery.isLoading ? 'Checking…' : statusConfig[uiStatus].label}</span>
              </div>
            </div>
          </div>
        </div>

        {uiStatus === 'connected' && (
          <div className="mt-4 pt-4 border-t space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">Connected accounts</span>
              <span className="font-medium">{meta?.ad_accounts_count ?? 0}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">Connected on</span>
              <span className="font-medium">{connectedOn}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">Token expires</span>
              <span className="font-medium">{expiresOn}</span>
            </div>
            {meta?.last_error && (
              <p className="text-sm text-red-400">{meta.last_error}</p>
            )}
          </div>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          {uiStatus === 'connected' || uiStatus === 'expired' ? (
            <>
              <button
                type="button"
                onClick={() => refreshMutation.mutate()}
                disabled={busy}
                className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border hover:bg-accent transition-colors disabled:opacity-50"
              >
                <ArrowPathIcon className={cn('h-4 w-4', refreshMutation.isPending && 'animate-spin')} />
                Refresh token
              </button>
              <button
                type="button"
                onClick={() => {
                  if (confirm('Disconnect Meta Ads from this workspace?')) {
                    disconnectMutation.mutate();
                  }
                }}
                disabled={busy}
                className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors disabled:opacity-50"
              >
                Disconnect
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => connectMutation.mutate()}
              disabled={busy || statusQuery.isLoading}
              className="flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {connectMutation.isPending ? (
                <ArrowPathIcon className="h-4 w-4 animate-spin" />
              ) : (
                <LinkIcon className="h-4 w-4" />
              )}
              Connect Meta Ads
            </button>
          )}
        </div>
      </div>

      <div className="rounded-xl border bg-muted/30 p-6">
        <h3 className="font-semibold mb-2">About this connection</h3>
        <ul className="space-y-2 text-sm text-muted-foreground">
          <li>You will sign in with Facebook and grant ads_read, ads_management, and business_management.</li>
          <li>Tokens are stored encrypted for this workspace only.</li>
          <li>Autopilot writes stay off unless an operator enables them separately.</li>
        </ul>
      </div>
    </div>
  );
}
