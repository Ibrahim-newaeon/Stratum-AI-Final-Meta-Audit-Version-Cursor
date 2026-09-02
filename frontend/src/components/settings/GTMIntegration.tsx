/**
 * Google Tag Manager Integration Settings Card
 *
 * Measurement & Verification only: GTM is used for TAG DEPLOYMENT of the
 * Meta Pixel, Meta Conversions API (server-side container) and the Stratum
 * tracking snippet, plus the CDP 'sgtm' source. It is never an ad channel.
 *
 * Tenant context comes from the shared API client headers; no props required.
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Copy,
  Eye,
  EyeOff,
  Loader2,
  Save,
  ShieldCheck,
  Trash2,
  XCircle,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { MeasurementConnectionStatus } from '@/api/measurement';
import {
  useDisconnectGTM,
  useGTMConfig,
  useGTMSnippets,
  useSaveGTMConfig,
  useVerifyGTM,
} from '@/api/measurement';

// =============================================================================
// Helpers
// =============================================================================

const GTM_COLOR = '#4285F4';
const GTM_CONTAINER_ID_RE = /^GTM-[A-Z0-9]{4,10}$/;

function getErrorMessage(err: unknown, fallback: string): string {
  const e = err as {
    response?: { data?: { message?: string; detail?: string; errors?: string[] } };
    message?: string;
  };
  return (
    e?.response?.data?.message ||
    e?.response?.data?.detail ||
    e?.response?.data?.errors?.[0] ||
    e?.message ||
    fallback
  );
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

function isHttpsUrl(value: string): boolean {
  try {
    const u = new URL(value);
    return u.protocol === 'https:';
  } catch {
    return false;
  }
}

function maskKey(value: string | null | undefined): string {
  if (!value) return '—';
  if (value.length <= 8) return '••••••••';
  return `${value.slice(0, 4)}••••••••${value.slice(-4)}`;
}

const inputClass =
  'w-full px-4 py-2 rounded-xl border border-white/10 glass bg-transparent text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary disabled:opacity-50';

// =============================================================================
// Sub-components
// =============================================================================

function StatusPill({ status, configured }: { status: MeasurementConnectionStatus; configured: boolean }) {
  const { t } = useTranslation();
  if (!configured) {
    return (
      <span className="px-2.5 py-1 rounded-full bg-gray-500/20 text-gray-400 text-xs font-medium border border-gray-500/30">
        {t('settings.notConfigured')}
      </span>
    );
  }
  const styles: Record<MeasurementConnectionStatus, string> = {
    connected: 'bg-green-500/20 text-green-400 border-green-500/30',
    error: 'bg-red-500/20 text-red-400 border-red-500/30',
    disconnected: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
  };
  const labels: Record<MeasurementConnectionStatus, string> = {
    connected: t('settings.configured'),
    error: t('settings.connectionFailed'),
    disconnected: t('settings.notConnected'),
  };
  return (
    <span className={cn('px-2.5 py-1 rounded-full text-xs font-medium border', styles[status])}>
      {labels[status]}
    </span>
  );
}

function CopyButton({ value, label }: { value: string; label?: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard may be unavailable (insecure context); fail silently
    }
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      disabled={!value}
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-white/10 hover:bg-white/5 text-xs disabled:opacity-50"
      aria-label={label ?? t('settings.copySnippet')}
    >
      {copied ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
      {copied ? t('settings.copied') : label ?? t('settings.copySnippet')}
    </button>
  );
}

function SnippetBlock({ title, code }: { title: string; code: string }) {
  return (
    <div className="rounded-lg border border-white/10 bg-black/30 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/10">
        <span className="text-xs font-medium">{title}</span>
        <CopyButton value={code} />
      </div>
      <pre className="p-3 text-[11px] font-mono text-cyan-300/90 overflow-x-auto whitespace-pre max-h-40">
        {code}
      </pre>
    </div>
  );
}

function ToggleRow({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex items-center justify-between gap-3 w-full px-3 py-2 rounded-lg border border-white/10 hover:bg-white/5 text-left"
    >
      <span className="text-sm min-w-0">{label}</span>
      <span
        className={cn(
          'relative w-10 h-5 rounded-full transition-colors shrink-0',
          checked ? 'bg-primary' : 'bg-white/10'
        )}
      >
        <span
          className={cn(
            'absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform',
            checked && 'translate-x-5'
          )}
        />
      </span>
    </button>
  );
}

/** Snippets panel; mounted only when GTM is configured. */
function GTMSnippetsPanel({ sourceKey }: { sourceKey: string | null }) {
  const { t } = useTranslation();
  const { data, isLoading, isError, error } = useGTMSnippets(true);

  return (
    <div className="space-y-3">
      <p className="text-sm font-medium">{t('settings.gtmSnippets')}</p>
      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="w-4 h-4 animate-spin" />
          {t('common.loading', 'Loading...')}
        </div>
      ) : isError || !data ? (
        <p className="text-xs text-red-400">{getErrorMessage(error, 'Failed to load snippets')}</p>
      ) : (
        <>
          {data.head_snippet && <SnippetBlock title="<head> (web container)" code={data.head_snippet} />}
          {data.body_snippet && <SnippetBlock title="<body> (noscript)" code={data.body_snippet} />}
          {data.stratum_snippet && (
            <SnippetBlock title="Stratum tracking snippet" code={data.stratum_snippet} />
          )}
          <div className="rounded-lg border border-white/10 bg-black/20 p-3 space-y-1.5 text-xs">
            <p className="font-medium">Server-side GTM (CDP source: {data.sgtm_config.cdp_source_type})</p>
            <p className="text-muted-foreground break-all">
              Transport URL: {data.sgtm_config.transport_url ?? '—'}
            </p>
            <p className="text-muted-foreground break-all">
              Stratum ingest: {data.sgtm_config.stratum_ingest_url}
            </p>
            <p className="text-muted-foreground">
              Meta CAPI: {data.sgtm_config.meta_capi_client}
              {data.sgtm_config.meta_pixel_id ? ` · pixel ${data.sgtm_config.meta_pixel_id}` : ''}
            </p>
            <div className="flex items-center justify-between gap-2 pt-1">
              <span className="font-mono text-muted-foreground">
                {data.sgtm_config.source_header}: {maskKey(data.sgtm_config.source_key ?? sourceKey)}
              </span>
              {(data.sgtm_config.source_key ?? sourceKey) && (
                <CopyButton value={(data.sgtm_config.source_key ?? sourceKey) as string} label="Copy key" />
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// =============================================================================
// Main component
// =============================================================================

export function GTMIntegration() {
  const { t } = useTranslation();

  const { data: config, isLoading } = useGTMConfig();
  const saveMutation = useSaveGTMConfig();
  const verifyMutation = useVerifyGTM();
  const disconnectMutation = useDisconnectGTM();

  // Form state
  const [webContainerId, setWebContainerId] = useState('');
  const [serverContainerUrl, setServerContainerUrl] = useState('');
  const [serverContainerId, setServerContainerId] = useState('');
  const [previewHeader, setPreviewHeader] = useState('');
  const [showPreviewHeader, setShowPreviewHeader] = useState(false);
  const [metaPixelId, setMetaPixelId] = useState('');
  const [deployMetaPixel, setDeployMetaPixel] = useState(true);
  const [deployMetaCapi, setDeployMetaCapi] = useState(true);
  const [deployStratumSnippet, setDeployStratumSnippet] = useState(true);
  const [showSnippets, setShowSnippets] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  const configured = !!config;
  const status: MeasurementConnectionStatus = config?.status ?? 'disconnected';

  // Hydrate form from stored config (never includes secrets)
  useEffect(() => {
    if (!config) return;
    setWebContainerId(config.web_container_id ?? '');
    setServerContainerUrl(config.server_container_url ?? '');
    setServerContainerId(config.server_container_id ?? '');
    setMetaPixelId(config.meta_pixel_id ?? '');
    setDeployMetaPixel(config.deploy_meta_pixel);
    setDeployMetaCapi(config.deploy_meta_capi);
    setDeployStratumSnippet(config.deploy_stratum_snippet);
    setDirty(false);
  }, [config]);

  const webIdTrimmed = webContainerId.trim().toUpperCase();
  const serverIdTrimmed = serverContainerId.trim().toUpperCase();
  const serverUrlTrimmed = serverContainerUrl.trim().replace(/\/+$/, '');

  const webIdValid = webIdTrimmed === '' || GTM_CONTAINER_ID_RE.test(webIdTrimmed);
  const serverIdValid = serverIdTrimmed === '' || GTM_CONTAINER_ID_RE.test(serverIdTrimmed);
  const serverUrlValid = serverUrlTrimmed === '' || isHttpsUrl(serverUrlTrimmed);
  const pixelIdValid = metaPixelId.trim() === '' || /^\d{6,20}$/.test(metaPixelId.trim());
  const hasAnyContainer = webIdTrimmed !== '' || serverUrlTrimmed !== '';

  const canSave =
    webIdValid && serverIdValid && serverUrlValid && pixelIdValid && hasAnyContainer && !saveMutation.isPending;

  const markDirty = () => {
    setDirty(true);
    setFormError(null);
    setSaveMessage(null);
    verifyMutation.reset();
  };

  const handleSave = async () => {
    if (!webIdValid || !serverIdValid) {
      setFormError('Container IDs must look like GTM-XXXXXXX (uppercase letters and digits).');
      return;
    }
    if (!serverUrlValid) {
      setFormError('Server-side tagging endpoint must be an https:// URL.');
      return;
    }
    if (!pixelIdValid) {
      setFormError('Meta Pixel ID must be numeric.');
      return;
    }
    if (!hasAnyContainer) {
      setFormError('Enter a web container ID and/or a server-side tagging endpoint.');
      return;
    }
    setFormError(null);
    try {
      await saveMutation.mutateAsync({
        web_container_id: webIdTrimmed || null,
        server_container_url: serverUrlTrimmed || null,
        server_container_id: serverIdTrimmed || null,
        ...(previewHeader.trim() ? { preview_header: previewHeader.trim() } : {}),
        meta_pixel_id: metaPixelId.trim() || null,
        deploy_meta_pixel: deployMetaPixel,
        deploy_meta_capi: deployMetaCapi,
        deploy_stratum_snippet: deployStratumSnippet,
        is_active: true,
      });
      // Never keep the secret around after a successful save
      setPreviewHeader('');
      setShowPreviewHeader(false);
      setDirty(false);
      setSaveMessage(t('settings.saved'));
    } catch (err) {
      setFormError(getErrorMessage(err, 'Failed to save GTM configuration'));
    }
  };

  const handleVerify = () => {
    setFormError(null);
    verifyMutation.mutate();
  };

  const handleDisconnect = () => {
    if (!confirm('Disconnect Google Tag Manager? Container settings will be removed.')) return;
    setFormError(null);
    disconnectMutation.mutate(undefined, {
      onSuccess: () => {
        setWebContainerId('');
        setServerContainerUrl('');
        setServerContainerId('');
        setPreviewHeader('');
        setMetaPixelId('');
        setDeployMetaPixel(true);
        setDeployMetaCapi(true);
        setDeployStratumSnippet(true);
        setShowSnippets(false);
        setSaveMessage(null);
        setDirty(false);
        verifyMutation.reset();
      },
      onError: (err) => setFormError(getErrorMessage(err, 'Failed to disconnect GTM')),
    });
  };

  const verifyResult = verifyMutation.data;

  const renderCheck = (ok: boolean | null, label: string) => (
    <span className="inline-flex items-center gap-1">
      {ok === null ? (
        <span className="w-3.5 h-3.5 rounded-full border border-white/20" />
      ) : ok ? (
        <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />
      ) : (
        <XCircle className="w-3.5 h-3.5 text-red-400" />
      )}
      {label}
    </span>
  );

  return (
    <div className="p-5 rounded-xl border border-white/10 glass card-3d space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div
            className="w-11 h-11 rounded-xl flex items-center justify-center shrink-0 bg-black/30"
            style={{ color: GTM_COLOR }}
          >
            <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor" aria-hidden="true">
              <path d="M12 2.5 21.5 12 12 21.5 2.5 12 12 2.5Zm0 4.2L6.7 12l5.3 5.3 5.3-5.3L12 6.7Z" />
              <circle cx="12" cy="12" r="2.2" opacity="0.8" />
            </svg>
          </div>
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h4 className="font-medium">{t('settings.gtm')}</h4>
              <StatusPill status={status} configured={configured} />
            </div>
            <p className="text-sm text-muted-foreground">{t('settings.gtmDesc')}</p>
          </div>
        </div>
        {isLoading && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />}
      </div>

      {/* Form */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium mb-1.5" htmlFor="gtm-web-container-id">
            {t('settings.gtmWebContainerId')}
          </label>
          <input
            id="gtm-web-container-id"
            type="text"
            autoComplete="off"
            placeholder="GTM-XXXXXXX"
            value={webContainerId}
            onChange={(e) => {
              setWebContainerId(e.target.value.toUpperCase());
              markDirty();
            }}
            className={cn(inputClass, 'uppercase', webContainerId && !webIdValid && 'border-red-500/50')}
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1.5" htmlFor="gtm-meta-pixel-id">
            {t('settings.gtmMetaPixelId')}
          </label>
          <input
            id="gtm-meta-pixel-id"
            type="text"
            inputMode="numeric"
            autoComplete="off"
            placeholder="123456789012345"
            value={metaPixelId}
            onChange={(e) => {
              setMetaPixelId(e.target.value.replace(/[^\d]/g, ''));
              markDirty();
            }}
            className={cn(inputClass, metaPixelId && !pixelIdValid && 'border-red-500/50')}
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1.5" htmlFor="gtm-server-container-url">
            {t('settings.gtmServerContainerUrl')}
          </label>
          <input
            id="gtm-server-container-url"
            type="url"
            autoComplete="off"
            placeholder="https://sgtm.yourdomain.com"
            value={serverContainerUrl}
            onChange={(e) => {
              setServerContainerUrl(e.target.value);
              markDirty();
            }}
            className={cn(inputClass, serverContainerUrl && !serverUrlValid && 'border-red-500/50')}
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1.5" htmlFor="gtm-server-container-id">
            {t('settings.gtmServerContainerId')}
          </label>
          <input
            id="gtm-server-container-id"
            type="text"
            autoComplete="off"
            placeholder="GTM-XXXXXXX"
            value={serverContainerId}
            onChange={(e) => {
              setServerContainerId(e.target.value.toUpperCase());
              markDirty();
            }}
            className={cn(inputClass, 'uppercase', serverContainerId && !serverIdValid && 'border-red-500/50')}
          />
        </div>
        <div className="md:col-span-2">
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-sm font-medium" htmlFor="gtm-preview-header">
              {t('settings.gtmPreviewHeader')}
            </label>
            <button
              type="button"
              onClick={() => setShowPreviewHeader((v) => !v)}
              className="text-muted-foreground hover:text-foreground"
              aria-label={showPreviewHeader ? 'Hide preview header' : 'Show preview header'}
            >
              {showPreviewHeader ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>
          {config?.has_preview_header && (
            <div className="flex items-center gap-2 text-xs text-green-400 mb-2">
              <CheckCircle2 className="w-3.5 h-3.5" />
              Stored (leave empty to keep the current header)
            </div>
          )}
          <input
            id="gtm-preview-header"
            type={showPreviewHeader ? 'text' : 'password'}
            autoComplete="new-password"
            placeholder="X-Gtm-Server-Preview value"
            value={previewHeader}
            onChange={(e) => {
              setPreviewHeader(e.target.value);
              markDirty();
            }}
            className={cn(inputClass, 'font-mono')}
          />
        </div>
      </div>

      {/* Deploy toggles */}
      <div className="grid grid-cols-1 gap-2">
        <ToggleRow
          label={t('settings.gtmDeployMetaPixel')}
          checked={deployMetaPixel}
          onChange={(v) => {
            setDeployMetaPixel(v);
            markDirty();
          }}
        />
        <ToggleRow
          label={t('settings.gtmDeployMetaCapi')}
          checked={deployMetaCapi}
          onChange={(v) => {
            setDeployMetaCapi(v);
            markDirty();
          }}
        />
        <ToggleRow
          label={t('settings.gtmDeployStratumSnippet')}
          checked={deployStratumSnippet}
          onChange={(v) => {
            setDeployStratumSnippet(v);
            markDirty();
          }}
        />
      </div>

      {/* Inline feedback */}
      {formError && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-sm text-red-400">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{formError}</span>
        </div>
      )}
      {saveMessage && !formError && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-green-500/10 border border-green-500/20 text-sm text-green-400">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{saveMessage}</span>
        </div>
      )}
      {verifyMutation.isError && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-sm text-red-400">
          <XCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>
            {t('settings.connectionFailed')}: {getErrorMessage(verifyMutation.error, 'Unknown error')}
          </span>
        </div>
      )}
      {verifyResult && (
        <div
          className={cn(
            'flex items-start gap-2 px-3 py-2 rounded-lg border text-sm',
            verifyResult.success
              ? 'bg-green-500/10 border-green-500/20 text-green-400'
              : 'bg-red-500/10 border-red-500/20 text-red-400'
          )}
        >
          {verifyResult.success ? (
            <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
          ) : (
            <XCircle className="w-4 h-4 mt-0.5 shrink-0" />
          )}
          <div>
            <p>{verifyResult.success ? t('settings.connectionVerified') : t('settings.connectionFailed')}</p>
            <p className="text-xs opacity-80">{verifyResult.message}</p>
            <div className="flex flex-wrap gap-3 text-xs mt-1">
              {renderCheck(verifyResult.web_container_ok, 'Web container')}
              {renderCheck(verifyResult.server_container_ok, 'Server container')}
            </div>
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-2">
        {configured && (
          <button
            type="button"
            onClick={handleVerify}
            disabled={verifyMutation.isPending}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl border border-white/10 hover:bg-white/5 text-sm disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {verifyMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {t('settings.testing')}
              </>
            ) : (
              <>
                <ShieldCheck className="w-4 h-4" />
                {t('settings.gtmVerify')}
              </>
            )}
          </button>
        )}
        <button
          type="button"
          onClick={handleSave}
          disabled={!canSave || (!dirty && configured)}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 text-sm disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saveMutation.isPending ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Save className="w-4 h-4" />
          )}
          {t('settings.saveConfiguration')}
        </button>
        {configured && (
          <button
            type="button"
            onClick={() => setShowSnippets((v) => !v)}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl border border-white/10 hover:bg-white/5 text-sm"
          >
            <Copy className="w-4 h-4" />
            {t('settings.gtmSnippets')}
          </button>
        )}
        {configured && (
          <button
            type="button"
            onClick={handleDisconnect}
            disabled={disconnectMutation.isPending}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl border border-red-500/30 hover:bg-red-500/10 text-red-400 text-sm disabled:opacity-50 ml-auto"
          >
            {disconnectMutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Trash2 className="w-4 h-4" />
            )}
            {t('settings.disconnect')}
          </button>
        )}
      </div>

      {/* Timestamps */}
      {configured && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs text-muted-foreground">
          <div>
            <span className="font-medium text-foreground/80">{t('settings.lastVerified')}:</span>{' '}
            {formatDateTime(config?.last_verified_at)}
            {config?.last_verify_success === false && config?.last_verify_message
              ? ` · ${config.last_verify_message}`
              : ''}
          </div>
          <div>
            <span className="font-medium text-foreground/80">CDP source:</span>{' '}
            {config?.cdp_source_id ? `sgtm · ${maskKey(config.cdp_source_key)}` : '—'}
          </div>
          {config?.last_error && (
            <div className="sm:col-span-2 text-red-400 break-words">{config.last_error}</div>
          )}
        </div>
      )}

      {/* Snippets panel */}
      {configured && showSnippets && <GTMSnippetsPanel sourceKey={config?.cdp_source_key ?? null} />}

      {/* Footnote */}
      <p className="text-[11px] text-muted-foreground border-t border-white/10 pt-3">
        {t('settings.measurementNotAdChannel')}
      </p>
    </div>
  );
}

export default GTMIntegration;
