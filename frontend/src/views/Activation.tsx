/**
 * Meta Activation Hub — required integration steps before full product use.
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
import { cn } from '@/lib/utils';

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

  if (isLoading || !status) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-teal-400" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-8 p-6">
      <div>
        <div className="mb-2 flex items-center gap-2 text-teal-400">
          <Shield className="h-5 w-5" />
          <span className="text-sm font-medium uppercase tracking-wide">Meta Integration</span>
        </div>
        <h1 className="text-3xl font-bold text-white">Complete your Meta setup</h1>
        <p className="mt-2 max-w-2xl text-white/60">
          Connecting Facebook and CAPI alone does not unlock every Stratum feature. Three
          credentials work together — finish all required steps so campaigns, audiences, and
          signals integrate properly.
        </p>
      </div>

      {/* Progress */}
      <div className="rounded-xl border border-white/10 bg-white/5 p-6">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-sm text-white/70">Required progress</span>
          <span className="text-sm font-medium text-teal-400">
            {status.required_done} / {status.required_total} complete
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-white/10">
          <div
            className="h-full rounded-full bg-gradient-to-r from-teal-500 to-teal-300 transition-all"
            style={{ width: `${status.progress_percent}%` }}
          />
        </div>
        {status.required_complete && (
          <p className="mt-3 flex items-center gap-2 text-sm text-emerald-400">
            <CheckCircle2 className="h-4 w-4" />
            All required Meta integrations are configured.
          </p>
        )}
      </div>

      {/* Credential explainer */}
      <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-5">
        <div className="flex gap-3">
          <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
          <div className="space-y-2 text-sm text-white/80">
            <p className="font-medium text-amber-200">Three different Meta tokens — not interchangeable</p>
            <ul className="list-inside list-disc space-y-1 text-white/60">
              <li>
                <strong className="text-white/80">OAuth (Connect Platforms)</strong> — user login for
                ads read, campaign discovery, insights
              </li>
              <li>
                <strong className="text-white/80">CAPI token + Pixel</strong> — server-side conversion
                events only
              </li>
              <li>
                <strong className="text-white/80">Marketing API System User token</strong> — Custom
                Audiences, audience sync, Marketing API writes (required below)
              </li>
            </ul>
          </div>
        </div>
      </div>

      {/* Steps */}
      <div className="space-y-4">
        {status.steps.map((step) => (
          <div
            key={step.id}
            className={cn(
              'rounded-xl border p-5 transition-colors',
              step.complete
                ? 'border-emerald-500/30 bg-emerald-500/5'
                : 'border-white/10 bg-white/[0.03]'
            )}
          >
            <div className="flex items-start gap-4">
              {step.complete ? (
                <CheckCircle2 className="mt-0.5 h-6 w-6 shrink-0 text-emerald-400" />
              ) : (
                <Circle className="mt-0.5 h-6 w-6 shrink-0 text-white/30" />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="font-semibold text-white">{step.title}</h2>
                  {step.required ? (
                    <span className="rounded bg-teal-500/20 px-2 py-0.5 text-xs text-teal-300">
                      Required
                    </span>
                  ) : (
                    <span className="rounded bg-white/10 px-2 py-0.5 text-xs text-white/50">
                      Optional
                    </span>
                  )}
                </div>
                <p className="mt-1 text-sm text-white/60">{step.description}</p>
                {!step.complete && step.id !== 'marketing_api_token' && (
                  <button
                    type="button"
                    onClick={() => navigate(step.action_path)}
                    className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-teal-400 hover:text-teal-300"
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

      {/* Marketing API token form */}
      <div
        id="marketing-token"
        className="rounded-xl border border-teal-500/20 bg-teal-500/5 p-6"
      >
        <div className="mb-4 flex items-center gap-2">
          <KeyRound className="h-5 w-5 text-teal-400" />
          <h2 className="text-lg font-semibold text-white">Marketing API System User token</h2>
        </div>
        <p className="mb-6 text-sm text-white/60">
          Create a System User in{' '}
          <a
            href="https://business.facebook.com/settings/system-users"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-teal-400 hover:underline"
          >
            Meta Business Settings
            <ExternalLink className="h-3 w-3" />
          </a>
          . Assign <code className="rounded bg-black/30 px-1">ads_management</code> on your ad
          account, generate a token, and paste it here with your{' '}
          <code className="rounded bg-black/30 px-1">act_…</code> ID.
        </p>

        <form onSubmit={handleSaveMarketingToken} className="space-y-4">
          <div>
            <label htmlFor="ad_account_id" className="mb-1 block text-sm text-white/70">
              Ad Account ID <span className="text-red-400">*</span>
            </label>
            <input
              id="ad_account_id"
              type="text"
              required
              placeholder="act_1234567890"
              value={adAccountId}
              onChange={(e) => setAdAccountId(e.target.value)}
              className="w-full rounded-lg border border-white/10 bg-black/30 px-4 py-2.5 text-white placeholder:text-white/30 focus:border-teal-500/50 focus:outline-none focus:ring-1 focus:ring-teal-500/50"
            />
          </div>
          <div>
            <label htmlFor="marketing_token" className="mb-1 block text-sm text-white/70">
              Marketing API access token <span className="text-red-400">*</span>
            </label>
            <input
              id="marketing_token"
              type="password"
              required
              autoComplete="off"
              placeholder="System User token with ads_management"
              value={accessToken}
              onChange={(e) => setAccessToken(e.target.value)}
              className="w-full rounded-lg border border-white/10 bg-black/30 px-4 py-2.5 text-white placeholder:text-white/30 focus:border-teal-500/50 focus:outline-none focus:ring-1 focus:ring-teal-500/50"
            />
          </div>
          <div>
            <label htmlFor="display_name" className="mb-1 block text-sm text-white/70">
              Label (optional)
            </label>
            <input
              id="display_name"
              type="text"
              placeholder="Primary ad account"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="w-full rounded-lg border border-white/10 bg-black/30 px-4 py-2.5 text-white placeholder:text-white/30 focus:border-teal-500/50 focus:outline-none focus:ring-1 focus:ring-teal-500/50"
            />
          </div>
          <button
            type="submit"
            disabled={saveToken.isPending}
            className="inline-flex items-center gap-2 rounded-lg bg-teal-500 px-5 py-2.5 text-sm font-medium text-black hover:bg-teal-400 disabled:opacity-50"
          >
            {saveToken.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Save Marketing API token
          </button>
        </form>

        {status.steps.find((s) => s.id === 'marketing_api_token')?.complete && (
          <p className="mt-4 flex items-center gap-2 text-sm text-emerald-400">
            <CheckCircle2 className="h-4 w-4" />
            Token saved for at least one ad account. Manage audiences in{' '}
            <Link to="/dashboard/cdp/audience-sync" className="underline hover:text-emerald-300">
              Audience Sync
            </Link>
            .
          </p>
        )}
      </div>
    </div>
  );
}
