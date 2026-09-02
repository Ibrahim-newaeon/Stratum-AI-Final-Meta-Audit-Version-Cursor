/**
 * Google Analytics 4 Integration Settings Card
 *
 * Measurement & Verification only: GA4 is a READ-ONLY, independent
 * revenue/conversion baseline (GA4 Data API, service account, scope
 * analytics.readonly) used for attribution variance, EMQ and the Trust Gate.
 * It is never an ad channel and Stratum never writes to it.
 *
 * Tenant context comes from the shared API client headers; no props required.
 */

import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertCircle,
  CheckCircle2,
  Eye,
  EyeOff,
  Loader2,
  Lock,
  RefreshCw,
  Save,
  Trash2,
  XCircle,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { GA4Config, GA4TestPayload, MeasurementConnectionStatus } from '@/api/measurement';
import {
  GA4_READONLY_SCOPE,
  useDisconnectGA4,
  useGA4Baseline,
  useGA4Config,
  useSaveGA4Config,
  useSyncGA4,
  useTestGA4Connection,
} from '@/api/measurement';

// =============================================================================
// Helpers
// =============================================================================

const GA4_COLOR = '#E37400';

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

function toISODate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function parseEventNames(value: string): string[] {
  const names = value
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  return names.length > 0 ? Array.from(new Set(names)) : ['purchase'];
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value);
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
    connected: t('settings.connected'),
    error: t('settings.connectionFailed'),
    disconnected: t('settings.notConnected'),
  };
  return (
    <span className={cn('px-2.5 py-1 rounded-full text-xs font-medium border', styles[status])}>
      {labels[status]}
    </span>
  );
}

/** Small 7-day baseline summary; mounted only when GA4 is configured. */
function GA4BaselineSummary() {
  const { t } = useTranslation();
  const { startDate, endDate } = useMemo(() => {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - 6);
    return { startDate: toISODate(start), endDate: toISODate(end) };
  }, []);

  const { data, isLoading, isError } = useGA4Baseline(startDate, endDate, false);

  return (
    <div className="rounded-xl border border-white/10 bg-black/20 p-4">
      <div className="flex items-center justify-between mb-3">
        <p className="text-sm font-medium">{t('settings.ga4Baseline')}</p>
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {startDate} → {endDate}
        </span>
      </div>
      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="w-4 h-4 animate-spin" />
          {t('common.loading', 'Loading...')}
        </div>
      ) : isError || !data ? (
        <p className="text-xs text-muted-foreground">
          No baseline rows yet. Run a sync to pull the last days from the GA4 Data API.
        </p>
      ) : (
        <div className="grid grid-cols-3 gap-3">
          <div>
            <p className="text-xs text-muted-foreground">Sessions</p>
            <p className="text-lg font-semibold">{data.sessions.toLocaleString()}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Conversions</p>
            <p className="text-lg font-semibold">{data.conversions.toLocaleString()}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Revenue</p>
            <p className="text-lg font-semibold">{formatCurrency(data.revenue)}</p>
          </div>
          <p className="col-span-3 text-[11px] text-muted-foreground">
            {data.days_with_data}/7 days with data
            {data.last_date ? ` · last ${data.last_date}` : ''}
          </p>
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Main component
// =============================================================================

export function GA4Integration() {
  const { t } = useTranslation();

  const { data: config, isLoading } = useGA4Config();
  const saveMutation = useSaveGA4Config();
  const testMutation = useTestGA4Connection();
  const syncMutation = useSyncGA4();
  const disconnectMutation = useDisconnectGA4();

  // Form state
  const [propertyId, setPropertyId] = useState('');
  const [measurementId, setMeasurementId] = useState('');
  const [conversionEvents, setConversionEvents] = useState('purchase');
  const [serviceAccountJson, setServiceAccountJson] = useState('');
  const [showJson, setShowJson] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  const configured = !!config;
  const status: MeasurementConnectionStatus = config?.status ?? 'disconnected';

  // Hydrate form from stored config (never includes secrets)
  useEffect(() => {
    if (!config) return;
    setPropertyId(config.property_id ?? '');
    setMeasurementId(config.measurement_id ?? '');
    setConversionEvents((config.conversion_event_names ?? ['purchase']).join(', '));
    setDirty(false);
  }, [config]);

  const propertyIdValid = /^\d{4,20}$/.test(propertyId.trim());
  const measurementIdValid =
    measurementId.trim() === '' || /^G-[A-Z0-9]{4,12}$/i.test(measurementId.trim());
  const jsonValid = useMemo(() => {
    if (!serviceAccountJson.trim()) return true;
    try {
      const parsed = JSON.parse(serviceAccountJson) as { type?: string; client_email?: string };
      return parsed?.type === 'service_account' && typeof parsed?.client_email === 'string';
    } catch {
      return false;
    }
  }, [serviceAccountJson]);

  const canSave =
    propertyIdValid &&
    measurementIdValid &&
    jsonValid &&
    (configured ? true : serviceAccountJson.trim().length > 0) &&
    !saveMutation.isPending;

  const markDirty = () => {
    setDirty(true);
    setFormError(null);
    setSaveMessage(null);
    testMutation.reset();
  };

  const handleSave = async () => {
    if (!propertyIdValid) {
      setFormError('Property ID must be numeric (found in GA4 Admin > Property settings).');
      return;
    }
    if (!measurementIdValid) {
      setFormError('Measurement ID must look like G-XXXXXXX.');
      return;
    }
    if (!jsonValid) {
      setFormError('Service account JSON is invalid (expected a "service_account" key file).');
      return;
    }
    setFormError(null);
    try {
      const saved: GA4Config = await saveMutation.mutateAsync({
        property_id: propertyId.trim(),
        measurement_id: measurementId.trim() || null,
        conversion_event_names: parseEventNames(conversionEvents),
        ...(serviceAccountJson.trim() ? { service_account_json: serviceAccountJson } : {}),
        is_active: true,
      });
      // Never keep the secret around after a successful save
      setServiceAccountJson('');
      setShowJson(false);
      setDirty(false);
      setSaveMessage(
        saved.service_account_email
          ? `${t('settings.saved')} · ${saved.service_account_email}`
          : t('settings.saved')
      );
    } catch (err) {
      setFormError(getErrorMessage(err, 'Failed to save GA4 configuration'));
    }
  };

  const handleTest = () => {
    setFormError(null);
    const payload: GA4TestPayload = {};
    if (propertyId.trim() && propertyIdValid) payload.property_id = propertyId.trim();
    if (serviceAccountJson.trim() && jsonValid) payload.service_account_json = serviceAccountJson;
    testMutation.mutate(payload);
  };

  const handleSync = () => {
    setFormError(null);
    syncMutation.mutate({});
  };

  const handleDisconnect = () => {
    if (!confirm('Disconnect Google Analytics 4? Stored credentials will be deleted.')) return;
    setFormError(null);
    disconnectMutation.mutate(undefined, {
      onSuccess: () => {
        setPropertyId('');
        setMeasurementId('');
        setConversionEvents('purchase');
        setServiceAccountJson('');
        setSaveMessage(null);
        setDirty(false);
        testMutation.reset();
        syncMutation.reset();
      },
      onError: (err) => setFormError(getErrorMessage(err, 'Failed to disconnect GA4')),
    });
  };

  const testResult = testMutation.data;
  const syncResult = syncMutation.data;

  return (
    <div className="p-5 rounded-xl border border-white/10 glass card-3d space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div
            className="w-11 h-11 rounded-xl flex items-center justify-center shrink-0 bg-black/30"
            style={{ color: GA4_COLOR }}
          >
            <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor" aria-hidden="true">
              <rect x="14" y="3" width="6" height="18" rx="3" />
              <rect x="9" y="9" width="6" height="12" rx="3" opacity="0.75" />
              <circle cx="7" cy="18" r="3" opacity="0.75" />
            </svg>
          </div>
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h4 className="font-medium">{t('settings.ga4')}</h4>
              <StatusPill status={status} configured={configured} />
            </div>
            <p className="text-sm text-muted-foreground">{t('settings.ga4Desc')}</p>
          </div>
        </div>
        {isLoading && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />}
      </div>

      {/* Read-only scope notice (fixed, not editable) */}
      <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-300">
        <Lock className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span className="break-all">
          {t('settings.ga4ReadOnlyScope')}
          <span className="sr-only"> ({GA4_READONLY_SCOPE})</span>
        </span>
      </div>

      {/* Form */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium mb-1.5" htmlFor="ga4-property-id">
            {t('settings.ga4PropertyId')}
          </label>
          <input
            id="ga4-property-id"
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            autoComplete="off"
            placeholder="123456789"
            value={propertyId}
            onChange={(e) => {
              setPropertyId(e.target.value.replace(/[^\d]/g, ''));
              markDirty();
            }}
            className={cn(inputClass, propertyId && !propertyIdValid && 'border-red-500/50')}
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1.5" htmlFor="ga4-measurement-id">
            {t('settings.ga4MeasurementId')}
          </label>
          <input
            id="ga4-measurement-id"
            type="text"
            autoComplete="off"
            placeholder="G-XXXXXXX"
            value={measurementId}
            onChange={(e) => {
              setMeasurementId(e.target.value.toUpperCase());
              markDirty();
            }}
            className={cn(inputClass, measurementId && !measurementIdValid && 'border-red-500/50')}
          />
        </div>
        <div className="md:col-span-2">
          <label className="block text-sm font-medium mb-1.5" htmlFor="ga4-conversion-events">
            {t('settings.ga4ConversionEvents')}
          </label>
          <input
            id="ga4-conversion-events"
            type="text"
            autoComplete="off"
            placeholder="purchase, generate_lead"
            value={conversionEvents}
            onChange={(e) => {
              setConversionEvents(e.target.value);
              markDirty();
            }}
            className={inputClass}
          />
          <p className="text-[11px] text-muted-foreground mt-1">
            Comma-separated GA4 event names counted as conversions (default: purchase).
          </p>
        </div>
        <div className="md:col-span-2">
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-sm font-medium" htmlFor="ga4-service-account-json">
              {t('settings.ga4ServiceAccountJson')}
            </label>
            <button
              type="button"
              onClick={() => setShowJson((v) => !v)}
              className="text-muted-foreground hover:text-foreground"
              aria-label={showJson ? 'Hide service account JSON' : 'Show service account JSON'}
            >
              {showJson ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>
          {config?.has_credentials && (
            <div className="flex items-center gap-2 text-xs text-green-400 mb-2">
              <CheckCircle2 className="w-3.5 h-3.5" />
              <span className="break-all">
                Stored: {config.service_account_email ?? 'service account'}
                {config.service_account_fingerprint && (
                  <span className="text-muted-foreground font-mono ml-1">
                    · fp {config.service_account_fingerprint}
                  </span>
                )}
              </span>
            </div>
          )}
          <textarea
            id="ga4-service-account-json"
            rows={showJson ? 6 : 3}
            autoComplete="off"
            spellCheck={false}
            placeholder={
              config?.has_credentials
                ? 'Paste a new key file to rotate credentials (leave empty to keep the stored key)'
                : '{ "type": "service_account", "client_email": "...", "private_key": "..." }'
            }
            value={serviceAccountJson}
            onChange={(e) => {
              setServiceAccountJson(e.target.value);
              markDirty();
            }}
            className={cn(
              inputClass,
              'font-mono text-xs resize-y',
              serviceAccountJson && !jsonValid && 'border-red-500/50'
            )}
            style={
              !showJson && serviceAccountJson
                ? ({ WebkitTextSecurity: 'disc' } as React.CSSProperties)
                : undefined
            }
          />
          <p className="text-[11px] text-muted-foreground mt-1">{t('settings.ga4ServiceAccountHint')}</p>
        </div>
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
          <span className="break-all">{saveMessage}</span>
        </div>
      )}
      {testMutation.isError && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-sm text-red-400">
          <XCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>
            {t('settings.connectionFailed')}: {getErrorMessage(testMutation.error, 'Unknown error')}
          </span>
        </div>
      )}
      {testResult && (
        <div
          className={cn(
            'flex items-start gap-2 px-3 py-2 rounded-lg border text-sm',
            testResult.success
              ? 'bg-green-500/10 border-green-500/20 text-green-400'
              : 'bg-red-500/10 border-red-500/20 text-red-400'
          )}
        >
          {testResult.success ? (
            <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
          ) : (
            <XCircle className="w-4 h-4 mt-0.5 shrink-0" />
          )}
          <div>
            <p>
              {testResult.success ? t('settings.connectionVerified') : t('settings.connectionFailed')}
              {testResult.property_id ? ` · property ${testResult.property_id}` : ''}
            </p>
            <p className="text-xs opacity-80">{testResult.message}</p>
            {testResult.success &&
              (testResult.sessions_last_7d != null || testResult.revenue_last_7d != null) && (
                <p className="text-xs opacity-80 mt-0.5">
                  Last 7 days: {testResult.sessions_last_7d ?? 0} sessions ·{' '}
                  {testResult.conversions_last_7d ?? 0} conversions ·{' '}
                  {formatCurrency(testResult.revenue_last_7d ?? 0)}
                </p>
              )}
          </div>
        </div>
      )}
      {syncMutation.isError && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-sm text-red-400">
          <XCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{getErrorMessage(syncMutation.error, 'Sync failed')}</span>
        </div>
      )}
      {syncResult && (
        <div
          className={cn(
            'flex items-start gap-2 px-3 py-2 rounded-lg border text-sm',
            syncResult.success
              ? 'bg-green-500/10 border-green-500/20 text-green-400'
              : 'bg-amber-500/10 border-amber-500/20 text-amber-300'
          )}
        >
          <RefreshCw className="w-4 h-4 mt-0.5 shrink-0" />
          <div>
            <p>
              {syncResult.rows_upserted.toLocaleString()} rows upserted
              {syncResult.start_date && syncResult.end_date
                ? ` (${syncResult.start_date} → ${syncResult.end_date})`
                : ''}
            </p>
            <p className="text-xs opacity-80">{syncResult.message}</p>
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={handleTest}
          disabled={testMutation.isPending || (!configured && !(propertyIdValid && jsonValid && serviceAccountJson.trim()))}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-xl border border-white/10 hover:bg-white/5 text-sm disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {testMutation.isPending ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              {t('settings.testing')}
            </>
          ) : (
            t('settings.testConnection')
          )}
        </button>
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
            onClick={handleSync}
            disabled={syncMutation.isPending || !config?.has_credentials}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl border border-white/10 hover:bg-white/5 text-sm disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {syncMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {t('settings.ga4Syncing')}
              </>
            ) : (
              <>
                <RefreshCw className="w-4 h-4" />
                {t('settings.ga4SyncNow')}
              </>
            )}
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
            <span className="font-medium text-foreground/80">{t('settings.ga4LastSync')}:</span>{' '}
            {formatDateTime(config?.last_sync_at)}
            {config?.last_sync_rows != null ? ` (${config.last_sync_rows} rows)` : ''}
          </div>
          <div>
            <span className="font-medium text-foreground/80">{t('settings.lastVerified')}:</span>{' '}
            {formatDateTime(config?.last_verified_at)}
            {config?.last_verify_success === false && config?.last_verify_message
              ? ` · ${config.last_verify_message}`
              : ''}
          </div>
          {config?.last_error && (
            <div className="sm:col-span-2 text-red-400 break-words">{config.last_error}</div>
          )}
        </div>
      )}

      {/* 7-day baseline */}
      {configured && config?.has_credentials && <GA4BaselineSummary />}

      {/* Footnote */}
      <p className="text-[11px] text-muted-foreground border-t border-white/10 pt-3">
        {t('settings.measurementNotAdChannel')}
      </p>
    </div>
  );
}

export default GA4Integration;
