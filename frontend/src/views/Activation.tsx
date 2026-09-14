/**
 * Meta Activation Hub — Evidence Room styling
 */

import { FormEvent, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AlertCircle, ArrowRight, CheckCircle2, Circle, ExternalLink, KeyRound, Loader2 } from 'lucide-react';
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

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    if (!adAccountId.trim() || !accessToken.trim()) {
      toast({ title: 'Missing fields', description: 'Ad account ID and Marketing API token are required.', variant: 'destructive' });
      return;
    }
    try {
      await saveToken.mutateAsync({
        ad_account_id: adAccountId.trim(),
        access_token: accessToken.trim(),
        ad_account_name: displayName.trim() || undefined,
      });
      setAccessToken('');
      toast({ title: 'Marketing API token saved', description: 'Custom Audiences can use this credential.' });
      refetch();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Could not save credentials.';
      toast({ title: 'Save failed', description: String(detail), variant: 'destructive' });
    }
  };

  const input = {
    background: 'var(--er-bg)',
    borderColor: 'var(--er-border)',
    color: 'var(--er-text)',
    borderRadius: 'var(--er-radius)',
  } as const;

  if (isLoading || !status) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin" style={{ color: 'var(--er-accent)' }} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8 px-6 py-8">
      <div>
        <p className="er-label">Meta integration</p>
        <h1 className="er-serif mt-2 text-4xl">Complete your Meta setup</h1>
        <p className="mt-3 text-sm leading-6" style={{ color: 'var(--er-muted)' }}>
          Facebook login and CAPI alone do not unlock every feature. Three credentials work together —
          OAuth, CAPI, and a Marketing API System User token.
        </p>
      </div>

      <div className="er-panel p-5">
        <div className="mb-3 flex justify-between text-sm">
          <span style={{ color: 'var(--er-muted)' }}>Required progress</span>
          <span className="er-mono">{status.required_done} / {status.required_total}</span>
        </div>
        <div className="h-1" style={{ background: 'var(--er-border)' }}>
          <div className="h-1" style={{ width: `${status.progress_percent}%`, background: 'var(--er-accent)' }} />
        </div>
      </div>

      <div className="er-tray flex gap-3 p-4 text-sm">
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" style={{ color: 'var(--er-accent)' }} />
        <div style={{ color: 'var(--er-muted)' }}>
          <p className="font-medium" style={{ color: 'var(--er-text)' }}>Tokens are not interchangeable</p>
          <p className="mt-1">OAuth reads ads · CAPI sends conversions · Marketing API System User creates Custom Audiences.</p>
        </div>
      </div>

      <div className="space-y-3">
        {status.steps.map((step) => (
          <div key={step.id} className="er-panel p-5">
            <div className="flex gap-3">
              {step.complete ? (
                <CheckCircle2 className="h-5 w-5 shrink-0" style={{ color: 'var(--er-pass)' }} />
              ) : (
                <Circle className="h-5 w-5 shrink-0" style={{ color: 'var(--er-muted)' }} />
              )}
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="font-medium">{step.title}</h2>
                  <span className="er-label">{step.required ? 'Required' : 'Optional'}</span>
                </div>
                <p className="mt-1 text-sm" style={{ color: 'var(--er-muted)' }}>{step.description}</p>
                {!step.complete && step.id !== 'marketing_api_token' && (
                  <button type="button" onClick={() => navigate(step.action_path)} className="mt-3 inline-flex items-center gap-1 text-sm font-medium" style={{ color: 'var(--er-accent)' }}>
                    Set up now <ArrowRight className="h-4 w-4" />
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div id="marketing-token" className="er-panel p-6">
        <div className="mb-4 flex items-center gap-2">
          <KeyRound className="h-5 w-5" style={{ color: 'var(--er-accent)' }} />
          <h2 className="text-lg font-medium">Marketing API System User token</h2>
        </div>
        <p className="mb-6 text-sm" style={{ color: 'var(--er-muted)' }}>
          Create a System User in{' '}
          <a href="https://business.facebook.com/settings/system-users" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1" style={{ color: 'var(--er-accent)' }}>
            Meta Business Settings <ExternalLink className="h-3 w-3" />
          </a>
          . Assign <span className="er-mono">ads_management</span> and paste the token with your <span className="er-mono">act_…</span> ID.
        </p>
        <form onSubmit={handleSave} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm" htmlFor="ad_account_id">Ad Account ID *</label>
            <input id="ad_account_id" required className="w-full border px-3 py-2.5 text-sm" style={input} placeholder="act_1234567890" value={adAccountId} onChange={(e) => setAdAccountId(e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-sm" htmlFor="marketing_token">Marketing API access token *</label>
            <input id="marketing_token" type="password" required autoComplete="off" className="w-full border px-3 py-2.5 text-sm" style={input} value={accessToken} onChange={(e) => setAccessToken(e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-sm" htmlFor="display_name">Label (optional)</label>
            <input id="display_name" className="w-full border px-3 py-2.5 text-sm" style={input} value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </div>
          <button type="submit" className="er-btn-primary" disabled={saveToken.isPending}>
            {saveToken.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Save Marketing API token
          </button>
        </form>
        {status.steps.find((s) => s.id === 'marketing_api_token')?.complete && (
          <p className="mt-4 flex items-center gap-2 text-sm" style={{ color: 'var(--er-pass)' }}>
            <CheckCircle2 className="h-4 w-4" />
            Token saved. Manage audiences in <Link to="/dashboard/cdp/audience-sync" className="underline">Audience Sync</Link>.
          </p>
        )}
      </div>
    </div>
  );
}
