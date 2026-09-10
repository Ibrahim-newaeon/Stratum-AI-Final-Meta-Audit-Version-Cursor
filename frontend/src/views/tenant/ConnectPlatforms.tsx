/**
 * Stratum AI - Connect Platforms Page
 *
 * OAuth connection management for Meta Ads (Facebook / Instagram / WhatsApp
 * placements share one Marketing API connection — AdPlatform is Meta-only).
 */

import { useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  ArrowPathIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  LinkIcon,
  XCircleIcon,
} from '@heroicons/react/24/outline';
import { cn } from '@/lib/utils';
import {
  type Platform,
  useAdAccounts,
  useConnectorStatus,
  useDisconnectPlatform,
  useRefreshToken,
  useStartConnection,
} from '@/api/campaignBuilder';

interface PlatformConnection {
  id: Platform;
  name: string;
  status: 'connected' | 'disconnected' | 'expired' | 'error';
  connectedAt?: string;
  expiresAt?: string;
  accountCount?: number;
  lastError?: string | null;
}

/** Single Meta Ads card — FB/IG/WA are channels on this connection, not separate OAuth products. */
const META_PLATFORM: PlatformConnection = {
  id: 'meta',
  name: 'Meta Ads',
  status: 'disconnected',
};

const statusConfig = {
  connected: {
    icon: CheckCircleIcon,
    color: 'text-emerald-600 dark:text-emerald-400',
    bgColor: 'bg-emerald-50 dark:bg-emerald-950/30',
    label: 'Connected',
  },
  disconnected: {
    icon: XCircleIcon,
    color: 'text-gray-400',
    bgColor: 'bg-gray-50 dark:bg-gray-900/30',
    label: 'Not Connected',
  },
  expired: {
    icon: ExclamationTriangleIcon,
    color: 'text-amber-600 dark:text-amber-400',
    bgColor: 'bg-amber-50 dark:bg-amber-950/30',
    label: 'Token Expired',
  },
  error: {
    icon: XCircleIcon,
    color: 'text-red-600 dark:text-red-400',
    bgColor: 'bg-red-50 dark:bg-red-950/30',
    label: 'Connection Error',
  },
};

export default function ConnectPlatforms() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const tid = parseInt(tenantId || '1', 10);
  const [connecting, setConnecting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const { data: metaStatus, isLoading: statusLoading } = useConnectorStatus(tid, 'meta');
  const { data: metaAccounts } = useAdAccounts(tid, 'meta');

  const startConnection = useStartConnection(tid);
  const refreshToken = useRefreshToken(tid);
  const disconnectPlatform = useDisconnectPlatform(tid);

  const platform: PlatformConnection = useMemo(() => {
    if (!metaStatus) {
      return META_PLATFORM;
    }
    return {
      ...META_PLATFORM,
      status: (metaStatus.status as PlatformConnection['status']) || 'disconnected',
      connectedAt: metaStatus.connected_at?.split('T')[0],
      accountCount: metaAccounts?.length ?? 0,
      lastError: metaStatus.last_error,
    };
  }, [metaStatus, metaAccounts]);

  const handleConnect = async () => {
    setConnecting(true);
    setActionError(null);
    try {
      const result = await startConnection.mutateAsync('meta');
      if (result.oauth_url) {
        window.location.href = result.oauth_url;
        return;
      }
      setActionError('OAuth URL missing from server response. Check META_APP_ID configuration.');
    } catch (error) {
      console.error('Failed to start connection:', error);
      const detail =
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Failed to start Meta OAuth. Ensure META_APP_ID and META_APP_SECRET are configured.';
      setActionError(detail);
    } finally {
      setConnecting(false);
    }
  };

  const handleDisconnect = async () => {
    if (!confirm('Disconnect Meta Ads? Campaign sync and autopilot writes will stop until reconnected.')) {
      return;
    }
    setActionError(null);
    try {
      await disconnectPlatform.mutateAsync('meta');
    } catch (error) {
      console.error('Failed to disconnect:', error);
      setActionError('Failed to disconnect Meta. Try again or contact support.');
    }
  };

  const handleRefresh = async () => {
    setActionError(null);
    try {
      await refreshToken.mutateAsync('meta');
    } catch (error) {
      console.error('Failed to refresh token:', error);
      const detail =
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Token refresh failed. You may need to reconnect.';
      setActionError(detail);
    }
  };

  const status = statusConfig[platform.status];
  const StatusIcon = status.icon;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Connect Platforms</h1>
        <p className="text-muted-foreground">
          Connect Meta Ads via OAuth to sync campaigns and manage ad accounts. Facebook, Instagram,
          and WhatsApp placements use this same Marketing API connection.
        </p>
        <p className="text-xs text-muted-foreground mt-2 flex items-center gap-1">
          <ExclamationTriangleIcon className="w-3 h-3" />
          <span>
            This grants access to <strong>ad accounts &amp; campaigns</strong>. For server-side
            conversion tracking (CAPI), go to{' '}
            <a href="/dashboard/capi-setup" className="text-primary hover:underline">
              CAPI Setup
            </a>
            . Independent measurement lives under{' '}
            <a href="/dashboard/settings" className="text-primary hover:underline">
              Settings → Measurement &amp; Verification (GA4 read-only, GTM tag deployment)
            </a>
            .
          </span>
        </p>
      </div>

      {actionError && (
        <div
          className="rounded-lg border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/30 px-4 py-3 text-sm text-red-700 dark:text-red-300"
          role="alert"
        >
          {actionError}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div
          className={cn(
            'rounded-xl border p-6 transition-all',
            platform.status === 'connected' ? 'bg-card shadow-card' : 'bg-muted/30'
          )}
        >
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-4">
              <div className="h-12 w-12 rounded-lg bg-gradient-to-br from-gray-100 to-gray-200 dark:from-gray-800 dark:to-gray-700 flex items-center justify-center">
                <span className="text-lg font-bold text-gray-600 dark:text-gray-300">M</span>
              </div>
              <div>
                <h3 className="font-semibold">{platform.name}</h3>
                <div className={cn('flex items-center gap-1 text-sm', status.color)}>
                  <StatusIcon className="h-4 w-4" />
                  <span>{statusLoading ? 'Checking…' : status.label}</span>
                </div>
              </div>
            </div>
          </div>

          {platform.status === 'connected' && (
            <div className="mt-4 pt-4 border-t space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Connected Accounts</span>
                <span className="font-medium">{platform.accountCount ?? 0}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Connected On</span>
                <span className="font-medium">{platform.connectedAt || '—'}</span>
              </div>
            </div>
          )}

          {platform.status === 'error' && platform.lastError && (
            <p className="mt-3 text-sm text-red-600 dark:text-red-400">{platform.lastError}</p>
          )}

          <div className="mt-4 flex gap-2">
            {platform.status === 'connected' || platform.status === 'expired' ? (
              <>
                <button
                  type="button"
                  onClick={handleRefresh}
                  className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border hover:bg-accent transition-colors"
                >
                  <ArrowPathIcon className="h-4 w-4" />
                  Refresh Token
                </button>
                <button
                  type="button"
                  onClick={handleDisconnect}
                  className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors"
                >
                  Disconnect
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={handleConnect}
                disabled={connecting}
                className="flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50"
              >
                {connecting ? (
                  <ArrowPathIcon className="h-4 w-4 animate-spin" />
                ) : (
                  <LinkIcon className="h-4 w-4" />
                )}
                Connect Meta Ads
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="rounded-xl border bg-muted/30 p-6">
        <h3 className="font-semibold mb-2">About Platform Connections</h3>
        <ul className="space-y-2 text-sm text-muted-foreground">
          <li>- One Meta OAuth connection covers Facebook, Instagram, and WhatsApp ad placements</li>
          <li>- OAuth tokens are encrypted at rest and can be refreshed from this page</li>
          <li>- Requires a configured Meta App (`META_APP_ID` / `META_APP_SECRET`) and App Review for `ads_read`</li>
          <li>- You can disconnect at any time; reconnect to refresh scopes after App Review</li>
        </ul>
      </div>
    </div>
  );
}
