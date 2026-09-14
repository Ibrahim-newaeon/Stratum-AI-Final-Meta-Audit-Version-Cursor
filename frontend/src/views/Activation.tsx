/**
 * Meta Activation Hub — required integration steps (Studio visual system).
 */

import { FormEvent, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Circle,
  ExternalLink,
  KeyRound,
  Loader2,
  Shield,
} from 'lucide-react';
import { useActivationStatus, useSaveMarketingToken } from '@/api/activation';
import { useToast } from '@/components/ui/use-toast';

export default function Activation() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const { data: status, isLoading, refetch } = useActivationStatus();
  const saveToken = useSaveMarketingToken();

  const [adAccountId, setAdAccountId] = useState('');
  const [accessToken, setAccessToken] = useState('');
  const [displayName, setDisplayName] = useState('');

  const handleSaveMarketingToken = async (e: FormEvent) => {
    e.preventDefault();
    if (!adAccountId.trim() || !accessToken.trim()) {
      toast({
        title: 'Missing fields',
        description: 'Ad account ID and Marketing API token are required.',
        variant: 'destructive',
      });
      return;
    }
    try {
      await saveToken.mutateAsync({
        ad_account_id: adAccountId.trim(),
        access_token: accessToken.trim(),
        ad_account_name: displayName.trim() || undefined,
      });
      setAccessToken('');
      toast({
        title: 'Marketing API token saved',
        description: 'Custom Audiences and audience sync can now use this credential.',
      });
      refetch();
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Could not save credentials.';
      toast({ title: 'Save failed', description: String(detail), variant: 'destructive' });
    }
  };

  const inputStyle = {
    background: 'var(--studio-search-bg)',
    borderColor: 'var(--studio-border)',
    color: 'var(--studio-text)',
  } as const;

  if (isLoading || !status) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin" style={{ color: 'var(--studio-accent)' }} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-8 px-6 py-8">
      <div>
        <div className="mb-2 flex items-center gap-2" style={{ color: 'var(--studio-accent)' }}>
          <Shield className="h-5 w-5" />
          <span className="text-sm font-medium uppercase tracking-wide">Meta Integration</span>
        </div>
        <h1 className="text-3xl font-semibold" style={{ color: 'var(--studio-text)' }}>
          Complete your Meta setup
        </h1>
        <p className="mt-2 max-w-2xl text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
          Connecting Facebook and CAPI alone does not unlock every Stratum feature. Three
          credentials work together — finish all required steps so campaigns, audiences, and
          signals integrate properly.
        </p>
      </div>

      <div className="studio-card p-6">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
            Required progress
          </span>
          <span className="text-sm font-medium" style={{ color: 'var(--studio-accent)' }}>
            {status.required_done} / {status.required_total} complete
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full" style={{ background: 'var(--studio-nav-active)' }}>
          <div
            className="h-full rounded-full transition-all"
            style={{
              width: `${status.progress_percent}%`,
              background: 'var(--studio-accent)',
            }}
          />
        </div>
      </div>

      <div className="studio-card p-5">
        <div className="flex gap-3">
          <AlertCircle className="mt-0.5 h-5 w-5 shrink-0" style={{ color: 'var(--studio-accent)' }} />
          <div className="space-y-2 text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
            <p className="font-medium" style={{ color: 'var(--studio-text)' }}>
              Three different Meta tokens — not interchangeable
            </p>
            <ul className="list-inside list-disc space-y-1">
              <li>
                <strong style={{ color: 'var(--studio-text)' }}>OAuth</strong> — ads read, discovery,
                insights
              </li>
              <li>
                <strong style={{ color: 'var(--studio-text)' }}>CAPI + Pixel</strong> — conversion
                events only
              </li>
              <li>
                <strong style={{ color: 'var(--studio-text)' }}>Marketing API System User token</strong>{' '}
                — Custom Audiences (required below)
              </li>
            </ul>
          </div>
        </div>
      </div>

      <div className="space-y-4">
        {status.steps.map((step) => (
          <div
            key={step.id}
            className="studio-card p-5"
            style={{
              background: step.complete ? 'var(--studio-success-wash)' : 'var(--studio-card)',
            }}
          >
            <div className="flex items-start gap-4">
              {step.complete ? (
                <CheckCircle2 className="mt-0.5 h-6 w-6 shrink-0" style={{ color: 'var(--studio-success)' }} />
              ) : (
                <Circle className="mt-0.5 h-6 w-6 shrink-0" style={{ color: 'var(--studio-text-secondary)' }} />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="font-semibold" style={{ color: 'var(--studio-text)' }}>
                    {step.title}
                  </h2>
                  <span
                    className="rounded px-2 py-0.5 text-xs"
                    style={{
                      background: 'var(--studio-nav-active)',
                      color: 'var(--studio-accent)',
                    }}
                  >
                    {step.required ? 'Required' : 'Optional'}
                  </span>
                </div>
                <p className="mt-1 text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
                  {step.description}
                </p>
                {!step.complete && step.id !== 'marketing_api_token' && (
                  <button
                    type="button"
                    onClick={() => navigate(step.action_path)}
                    className="mt-3 inline-flex items-center gap-1 text-sm font-medium"
                    style={{ color: 'var(--studio-accent)' }}
                  >
                    Set up now
                    <ArrowRight className="h-4 w-4" />
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div id="marketing-token" className="studio-card p-6">
        <div className="mb-4 flex items-center gap-2">
          <KeyRound className="h-5 w-5" style={{ color: 'var(--studio-accent)' }} />
          <h2 className="text-lg font-semibold" style={{ color: 'var(--studio-text)' }}>
            Marketing API System User token
          </h2>
        </div>
        <p className="mb-6 text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
          Create a System User in{' '}
          <a
            href="https://business.facebook.com/settings/system-users"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1"
            style={{ color: 'var(--studio-accent)' }}
          >
            Meta Business Settings
            <ExternalLink className="h-3 w-3" />
          </a>
          . Assign <code className="rounded px-1" style={{ background: 'var(--studio-nav-active)' }}>ads_management</code> on
          your ad account and paste the token with your{' '}
          <code className="rounded px-1" style={{ background: 'var(--studio-nav-active)' }}>act_…</code> ID.
        </p>

        <form onSubmit={handleSaveMarketingToken} className="space-y-4">
          <div>
            <label htmlFor="ad_account_id" className="mb-1 block text-sm" style={{ color: 'var(--studio-text)' }}>
              Ad Account ID *
            </label>
            <input
              id="ad_account_id"
              type="text"
              required
              placeholder="act_1234567890"
              value={adAccountId}
              onChange={(e) => setAdAccountId(e.target.value)}
              className="studio-search w-full rounded-[10px] border px-4 py-2.5 text-sm"
              style={inputStyle}
            />
          </div>
          <div>
            <label htmlFor="marketing_token" className="mb-1 block text-sm" style={{ color: 'var(--studio-text)' }}>
              Marketing API access token *
            </label>
            <input
              id="marketing_token"
              type="password"
              required
              autoComplete="off"
              placeholder="System User token with ads_management"
              value={accessToken}
              onChange={(e) => setAccessToken(e.target.value)}
              className="studio-search w-full rounded-[10px] border px-4 py-2.5 text-sm"
              style={inputStyle}
            />
          </div>
          <div>
            <label htmlFor="display_name" className="mb-1 block text-sm" style={{ color: 'var(--studio-text)' }}>
              Label (optional)
            </label>
            <input
              id="display_name"
              type="text"
              placeholder="Primary ad account"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="studio-search w-full rounded-[10px] border px-4 py-2.5 text-sm"
              style={inputStyle}
            />
          </div>
          <button type="submit" disabled={saveToken.isPending} className="studio-btn-primary">
            {saveToken.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Save Marketing API token
          </button>
        </form>

        {status.steps.find((s) => s.id === 'marketing_api_token')?.complete && (
          <p className="mt-4 flex items-center gap-2 text-sm" style={{ color: 'var(--studio-success)' }}>
            <CheckCircle2 className="h-4 w-4" />
            Token saved. Manage audiences in{' '}
            <Link to="/dashboard/cdp/audience-sync" className="underline">
              Audience Sync
            </Link>
            .
          </p>
        )}
      </div>
    </div>
  );
}
