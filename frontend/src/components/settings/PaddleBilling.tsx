/**
 * Paddle Billing Settings Card (Settings > Billing)
 *
 * Live tenant subscription state from GET /billing/subscription and the
 * public billing configuration from GET /billing/config. Actions:
 *   - Manage billing / Update payment method -> Paddle customer portal
 *   - Choose plan / Upgrade plan             -> Paddle.js overlay checkout
 *                                              (new subscription) or
 *                                              POST /billing/upgrade (existing)
 *   - Cancel / Resume subscription           -> POST /billing/cancel|reactivate
 *   - Invoices table                         -> GET /billing/transactions +
 *                                              invoice PDF URL on demand
 *
 * Paddle.js v2 is loaded lazily from the Paddle CDN (src/lib/paddle.ts); the
 * client-side token always comes from the API, never from a Vite env var.
 * Tenant context comes from the shared API client headers; no props required.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  CheckCircle2,
  CreditCard,
  Download,
  ExternalLink,
  Loader2,
  Lock,
  RotateCcw,
  ShieldCheck,
  XCircle,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type {
  BillingSubscription,
  BillingTransaction,
  SubscriptionStatus,
  TierName,
  TierPriceInfo,
} from '@/api/billing';
import {
  billingQueryKeys,
  fetchTransactionInvoiceUrl,
  getBillingErrorMessage,
  useBillingConfig,
  useBillingSubscription,
  useBillingTransactions,
  useCancelSubscription,
  useCreateCheckoutSession,
  useCreatePortalSession,
  useReactivateSubscription,
  useUpgradeSubscription,
} from '@/api/billing';
import type { PaddleEvent } from '@/lib/paddle';
import { initializePaddle, openPaddleCheckout, resolveEnvironment } from '@/lib/paddle';

// =============================================================================
// Helpers
// =============================================================================

/** Paddle brand yellow (see getPlatformColor in lib/utils). */
const PADDLE_COLOR = '#FDDD35';

const TIER_ORDER: TierName[] = ['starter', 'professional', 'enterprise'];

/** Statuses that count as a live (billable) subscription. */
const LIVE_STATUSES: SubscriptionStatus[] = ['active', 'trialing', 'past_due'];

const CHECKOUT_SUCCESS_PATH = '/dashboard/billing/success';
const BILLING_SETTINGS_PATH = '/dashboard/settings?tab=billing';

function absoluteUrl(path: string): string {
  return `${window.location.origin}${path}`;
}

function openInNewTab(url: string): void {
  window.open(url, '_blank', 'noopener');
}

function formatDate(value: string | null | undefined, locale?: string): string {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(locale, { year: 'numeric', month: 'short', day: 'numeric' });
}

function formatMinorAmount(amountMinor: number, currency: string, locale?: string): string {
  const code = (currency || 'USD').toUpperCase();
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: code,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amountMinor / 100);
  } catch {
    return `${(amountMinor / 100).toFixed(2)} ${code}`;
  }
}

function formatTierPrice(tier: TierPriceInfo | undefined, perMonth: string, locale?: string): string {
  if (!tier || tier.price == null) return '—';
  const code = (tier.currency || 'USD').toUpperCase();
  let amount: string;
  try {
    amount = new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: code,
      maximumFractionDigits: 0,
    }).format(tier.price);
  } catch {
    amount = `${tier.price} ${code}`;
  }
  return `${amount}${perMonth}`;
}

function isLive(status: SubscriptionStatus | null | undefined): boolean {
  return !!status && LIVE_STATUSES.includes(status);
}

const primaryButtonClass =
  'inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 text-sm disabled:opacity-50 disabled:cursor-not-allowed';
const secondaryButtonClass =
  'inline-flex items-center gap-2 px-4 py-2 rounded-xl border border-white/10 hover:bg-white/5 text-sm disabled:opacity-50 disabled:cursor-not-allowed';
const dangerButtonClass =
  'inline-flex items-center gap-2 px-4 py-2 rounded-xl border border-red-500/30 hover:bg-red-500/10 text-red-400 text-sm disabled:opacity-50 disabled:cursor-not-allowed';

// =============================================================================
// Sub-components
// =============================================================================

function StatusPill({
  configured,
  subscription,
}: {
  configured: boolean;
  subscription: BillingSubscription | undefined;
}) {
  const { t } = useTranslation();

  if (!configured) {
    return (
      <span className="px-2.5 py-1 rounded-full bg-gray-500/20 text-gray-400 text-xs font-medium border border-gray-500/30">
        {t('settings.notConfigured')}
      </span>
    );
  }

  const status = subscription?.has_subscription ? subscription.status : null;
  if (!status) {
    return (
      <span className="px-2.5 py-1 rounded-full bg-gray-500/20 text-gray-400 text-xs font-medium border border-gray-500/30">
        {t('settings.statusNone')}
      </span>
    );
  }

  const styles: Record<SubscriptionStatus, string> = {
    active: 'bg-green-500/20 text-green-400 border-green-500/30',
    trialing: 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30',
    past_due: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
    paused: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
    canceled: 'bg-red-500/20 text-red-400 border-red-500/30',
  };
  const labels: Record<SubscriptionStatus, string> = {
    active: t('settings.statusActive'),
    trialing: t('settings.statusTrialing'),
    past_due: t('settings.statusPastDue'),
    paused: t('settings.statusPaused'),
    canceled: t('settings.statusCanceled'),
  };

  return (
    <span className={cn('px-2.5 py-1 rounded-full text-xs font-medium border', styles[status])}>
      {labels[status]}
    </span>
  );
}

function InlineError({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-sm text-red-400">
      <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
      <span className="break-words">{message}</span>
    </div>
  );
}

function InlineSuccess({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-green-500/10 border border-green-500/20 text-sm text-green-400">
      <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
      <span className="break-words">{message}</span>
    </div>
  );
}

function TransactionsTable({
  transactions,
  isLoading,
  onError,
}: {
  transactions: BillingTransaction[];
  isLoading: boolean;
  onError: (message: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const handleDownload = async (transactionId: string) => {
    setDownloadingId(transactionId);
    try {
      const url = await fetchTransactionInvoiceUrl(transactionId);
      openInNewTab(url);
    } catch (err) {
      onError(getBillingErrorMessage(err, t('settings.downloadInvoice')));
    } finally {
      setDownloadingId(null);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="w-4 h-4 animate-spin" />
        {t('common.loading', 'Loading...')}
      </div>
    );
  }

  if (transactions.length === 0) {
    return <p className="text-sm text-muted-foreground">{t('settings.noInvoices')}</p>;
  }

  return (
    <div className="rounded-xl border border-white/10 overflow-x-auto">
      <table className="w-full min-w-[520px]">
        <thead className="bg-white/5">
          <tr>
            <th className="p-3 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t('settings.invoices')}
            </th>
            <th className="p-3 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t('settings.date')}
            </th>
            <th className="p-3 text-right text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t('settings.amount')}
            </th>
            <th className="p-3 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t('settings.subscriptionStatus')}
            </th>
            <th className="p-3 text-right" />
          </tr>
        </thead>
        <tbody className="divide-y divide-white/10">
          {transactions.map((txn) => (
            <tr key={txn.id}>
              <td className="p-3 text-sm font-mono">{txn.invoice_number ?? txn.id}</td>
              <td className="p-3 text-sm">{formatDate(txn.billed_at ?? txn.created_at, i18n.language)}</td>
              <td className="p-3 text-sm text-right">
                {formatMinorAmount(txn.amount_minor, txn.currency, i18n.language)}
              </td>
              <td className="p-3 text-sm capitalize">{txn.status.replace(/_/g, ' ')}</td>
              <td className="p-3 text-right">
                <button
                  type="button"
                  onClick={() => void handleDownload(txn.id)}
                  disabled={downloadingId === txn.id}
                  className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline disabled:opacity-50"
                  aria-label={t('settings.downloadInvoice')}
                >
                  {downloadingId === txn.id ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Download className="w-4 h-4" />
                  )}
                  <span className="hidden sm:inline">{t('settings.downloadInvoice')}</span>
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// =============================================================================
// Main component
// =============================================================================

export function PaddleBilling() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();

  const { data: config, isLoading: configLoading, error: configError } = useBillingConfig();
  const {
    data: subscription,
    isLoading: subscriptionLoading,
    error: subscriptionError,
  } = useBillingSubscription();

  const configured = !!config?.paddle_configured && !!subscription?.paddle_configured;
  const hasCustomer = !!subscription?.has_customer;
  const hasSubscription = !!subscription?.has_subscription;
  const liveSubscription = hasSubscription && isLive(subscription?.status);

  const { data: transactions = [], isLoading: transactionsLoading } = useBillingTransactions(
    10,
    configured && hasCustomer
  );

  const checkoutMutation = useCreateCheckoutSession();
  const portalMutation = useCreatePortalSession();
  const cancelMutation = useCancelSubscription();
  const reactivateMutation = useReactivateSubscription();
  const upgradeMutation = useUpgradeSubscription();

  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [showPlanPicker, setShowPlanPicker] = useState(false);
  const [selectedTier, setSelectedTier] = useState<TierName | null>(null);
  const [paddleReady, setPaddleReady] = useState(false);
  const [paddleError, setPaddleError] = useState<string | null>(null);
  const [checkoutOpening, setCheckoutOpening] = useState(false);
  const [portalAction, setPortalAction] = useState<'portal' | 'payment' | null>(null);

  // Tier catalogue (ordered) and the current tier
  const tiers = useMemo(() => {
    const byTier = new Map<TierName, TierPriceInfo>();
    (config?.tiers ?? []).forEach((tier) => byTier.set(tier.tier, tier));
    return TIER_ORDER.map((name) => byTier.get(name)).filter((x): x is TierPriceInfo => !!x);
  }, [config?.tiers]);

  const currentTierName: TierName | null = useMemo(() => {
    if (subscription?.tier) return subscription.tier;
    const plan = subscription?.plan;
    return plan && (TIER_ORDER as string[]).includes(plan) ? (plan as TierName) : null;
  }, [subscription?.tier, subscription?.plan]);

  const currentTier = tiers.find((tier) => tier.tier === currentTierName);
  const currentPlanLabel =
    currentTier?.name ??
    (currentTierName
      ? currentTierName.charAt(0).toUpperCase() + currentTierName.slice(1)
      : subscription?.plan
        ? subscription.plan.charAt(0).toUpperCase() + subscription.plan.slice(1)
        : '—');

  const nextBillingDate = subscription?.next_billed_at ?? subscription?.current_period_end ?? null;

  // Paddle.js event handling (checkout.completed -> refresh subscription state)
  const handlePaddleEvent = useCallback(
    (event: PaddleEvent) => {
      if (event.name === 'checkout.completed') {
        setActionMessage(t('settings.checkoutSuccess'));
        void queryClient.invalidateQueries({ queryKey: billingQueryKeys.subscription });
        void queryClient.invalidateQueries({ queryKey: ['billing', 'transactions'] });
      }
    },
    [queryClient, t]
  );

  // Load + initialise Paddle.js as soon as the public config is available
  useEffect(() => {
    if (!config?.paddle_configured || !config.client_token) return;
    let cancelled = false;
    initializePaddle({
      token: config.client_token,
      environment: resolveEnvironment(config.environment),
      eventCallback: handlePaddleEvent,
    })
      .then(() => {
        if (!cancelled) {
          setPaddleReady(true);
          setPaddleError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setPaddleReady(false);
          setPaddleError(getBillingErrorMessage(err, 'Failed to load Paddle.js'));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [config?.paddle_configured, config?.client_token, config?.environment, handlePaddleEvent]);

  const resetFeedback = () => {
    setActionError(null);
    setActionMessage(null);
  };

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  const handleOpenPortal = async (target: 'portal' | 'payment') => {
    resetFeedback();
    setPortalAction(target);
    try {
      const session = await portalMutation.mutateAsync({ return_url: absoluteUrl(BILLING_SETTINGS_PATH) });
      const url =
        target === 'payment'
          ? (session.update_payment_method_url ?? session.portal_url)
          : session.portal_url;
      openInNewTab(url);
    } catch (err) {
      setActionError(getBillingErrorMessage(err, t('settings.manageBilling')));
    } finally {
      setPortalAction(null);
    }
  };

  const handleCheckout = async () => {
    if (!selectedTier || !config) return;
    resetFeedback();
    setCheckoutOpening(true);
    try {
      const session = await checkoutMutation.mutateAsync({
        tier: selectedTier,
        success_url: absoluteUrl(CHECKOUT_SUCCESS_PATH),
      });
      // Memoised per token: a no-op when the config already initialised Paddle.js
      await initializePaddle({
        token: session.client_token || config.client_token || '',
        environment: resolveEnvironment(session.environment),
        eventCallback: handlePaddleEvent,
      });
      await openPaddleCheckout(session, { locale: i18n.language?.split('-')[0] });
      setShowPlanPicker(false);
    } catch (err) {
      setActionError(getBillingErrorMessage(err, t('settings.openCheckout')));
    } finally {
      setCheckoutOpening(false);
    }
  };

  const handleChangePlan = async () => {
    if (!selectedTier) return;
    resetFeedback();
    try {
      await upgradeMutation.mutateAsync({ new_tier: selectedTier, prorate: true });
      setActionMessage(t('settings.saved'));
      setShowPlanPicker(false);
    } catch (err) {
      setActionError(getBillingErrorMessage(err, t('settings.upgradePlan')));
    }
  };

  const handleCancel = async () => {
    if (!confirm(t('settings.cancelSubscriptionConfirm'))) return;
    resetFeedback();
    try {
      await cancelMutation.mutateAsync({ at_period_end: true });
      setActionMessage(t('settings.cancelAtPeriodEnd'));
    } catch (err) {
      setActionError(getBillingErrorMessage(err, t('settings.cancelSubscription')));
    }
  };

  const handleResume = async () => {
    resetFeedback();
    try {
      await reactivateMutation.mutateAsync();
      setActionMessage(t('settings.statusActive'));
    } catch (err) {
      setActionError(getBillingErrorMessage(err, t('settings.resumeSubscription')));
    }
  };

  const openPlanPicker = () => {
    resetFeedback();
    if (liveSubscription) {
      // Existing subscription: the user must pick a different tier
      setSelectedTier(null);
    } else {
      // New checkout: preselect the tenant's plan (or Professional) when it has a Paddle price
      const preferred: TierName[] = currentTierName
        ? [currentTierName, 'professional', 'starter', 'enterprise']
        : ['professional', 'starter', 'enterprise'];
      setSelectedTier(preferred.find((tier) => !!config?.price_ids[tier]) ?? null);
    }
    setShowPlanPicker(true);
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const isLoading = configLoading || subscriptionLoading;
  const loadError = configError ?? subscriptionError;
  const perMonth = t('settings.perMonth');

  const planChangeViaApi = liveSubscription; // existing subscription -> PATCH via API
  const confirmDisabled =
    !selectedTier ||
    selectedTier === (liveSubscription ? currentTierName : null) ||
    (planChangeViaApi ? upgradeMutation.isPending : !paddleReady || checkoutOpening);

  return (
    <div className="p-5 rounded-xl border border-white/10 glass card-3d space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div
            className="w-11 h-11 rounded-xl flex items-center justify-center shrink-0 bg-black/30"
            style={{ color: PADDLE_COLOR }}
          >
            <CreditCard className="w-6 h-6" aria-hidden="true" />
          </div>
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h4 className="font-medium">{t('settings.paddleBilling')}</h4>
              <StatusPill configured={configured} subscription={subscription} />
            </div>
            <p className="text-sm text-muted-foreground flex items-center gap-1.5">
              <ShieldCheck className="w-3.5 h-3.5" />
              {t('settings.securePaymentsViaPaddle')}
              {config?.environment === 'sandbox' && (
                <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 text-[10px] uppercase tracking-wider">
                  sandbox
                </span>
              )}
            </p>
          </div>
        </div>
        {isLoading && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />}
      </div>

      {/* Load errors (API unreachable etc.) */}
      {loadError && <InlineError message={getBillingErrorMessage(loadError, 'Failed to load billing')} />}

      {/* Not configured notice */}
      {!isLoading && !loadError && !configured && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-sm text-amber-300">
          <Lock className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{t('settings.billingNotConfigured')}</span>
        </div>
      )}

      {/* Current plan */}
      {!isLoading && !loadError && (
        <div className="p-4 rounded-xl border border-primary/20 bg-primary/5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">{t('settings.currentPlan')}</p>
              <p className="font-medium text-lg">
                {currentPlanLabel}
                {currentTier && currentTier.price != null && (
                  <span className="text-sm text-muted-foreground ml-2">
                    {formatTierPrice(currentTier, perMonth, i18n.language)}
                  </span>
                )}
              </p>
              {hasSubscription && (
                <div className="text-sm text-muted-foreground space-y-0.5">
                  {subscription?.status === 'trialing' && subscription.trial_end ? (
                    <p>
                      {t('settings.trialEndsOn')}: {formatDate(subscription.trial_end, i18n.language)}
                    </p>
                  ) : null}
                  {nextBillingDate && !subscription?.cancel_at_period_end && (
                    <p>
                      {t('settings.nextBillingDate')}: {formatDate(nextBillingDate, i18n.language)}
                    </p>
                  )}
                  {subscription?.cancel_at_period_end && (
                    <p className="text-amber-300">
                      {t('settings.cancelAtPeriodEnd')}
                      {subscription.current_period_end
                        ? ` · ${formatDate(subscription.current_period_end, i18n.language)}`
                        : ''}
                    </p>
                  )}
                </div>
              )}
              {configured && !hasSubscription && (
                <p className="text-sm text-muted-foreground">{t('settings.noSubscription')}</p>
              )}
            </div>
            {configured && (
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={openPlanPicker}
                  disabled={tiers.length === 0 || showPlanPicker}
                  className={primaryButtonClass}
                >
                  {liveSubscription ? t('settings.upgradePlan') : t('settings.choosePlan')}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Plan picker */}
      {configured && showPlanPicker && (
        <div className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {tiers.map((tier) => {
              const isCurrent = liveSubscription && tier.tier === currentTierName;
              const priceId = config?.price_ids[tier.tier] ?? null;
              const purchasable = !!priceId || planChangeViaApi;
              const selected = selectedTier === tier.tier;
              return (
                <button
                  key={tier.tier}
                  type="button"
                  disabled={isCurrent || !purchasable}
                  onClick={() => setSelectedTier(tier.tier)}
                  className={cn(
                    'p-4 rounded-xl border text-left transition-all',
                    selected ? 'border-primary bg-primary/10' : 'border-white/10 hover:border-white/20',
                    (isCurrent || !purchasable) && 'opacity-60 cursor-not-allowed'
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="font-medium">{tier.name}</p>
                    {isCurrent && (
                      <span className="text-[10px] uppercase tracking-wider text-primary">
                        {t('settings.currentPlan')}
                      </span>
                    )}
                  </div>
                  <p className="text-sm mt-1">
                    {tier.price != null ? formatTierPrice(tier, perMonth, i18n.language) : '—'}
                  </p>
                  {tier.description && (
                    <p className="text-xs text-muted-foreground mt-1">{tier.description}</p>
                  )}
                </button>
              );
            })}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void (planChangeViaApi ? handleChangePlan() : handleCheckout())}
              disabled={confirmDisabled}
              className={primaryButtonClass}
            >
              {checkoutOpening || upgradeMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Lock className="w-4 h-4" />
              )}
              {planChangeViaApi ? t('settings.changePlan') : t('settings.openCheckout')}
            </button>
            <button
              type="button"
              onClick={() => setShowPlanPicker(false)}
              className={secondaryButtonClass}
            >
              {t('common.cancel', 'Cancel')}
            </button>
            {!planChangeViaApi && !paddleReady && !paddleError && (
              <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                <Loader2 className="w-3 h-3 animate-spin" />
                Paddle.js
              </span>
            )}
          </div>
          {paddleError && <InlineError message={paddleError} />}
        </div>
      )}

      {/* Inline feedback */}
      {actionError && <InlineError message={actionError} />}
      {actionMessage && !actionError && <InlineSuccess message={actionMessage} />}

      {/* Subscription actions */}
      {configured && hasCustomer && (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void handleOpenPortal('portal')}
            disabled={portalMutation.isPending}
            className={secondaryButtonClass}
          >
            {portalAction === 'portal' ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <ExternalLink className="w-4 h-4" />
            )}
            {t('settings.manageBilling')}
          </button>
          {hasSubscription && (
            <button
              type="button"
              onClick={() => void handleOpenPortal('payment')}
              disabled={portalMutation.isPending}
              className={secondaryButtonClass}
            >
              {portalAction === 'payment' ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <CreditCard className="w-4 h-4" />
              )}
              {t('settings.updatePaymentMethod')}
            </button>
          )}
          {liveSubscription && subscription?.cancel_at_period_end && (
            <button
              type="button"
              onClick={() => void handleResume()}
              disabled={reactivateMutation.isPending}
              className={secondaryButtonClass}
            >
              {reactivateMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <RotateCcw className="w-4 h-4" />
              )}
              {t('settings.resumeSubscription')}
            </button>
          )}
          {liveSubscription && !subscription?.cancel_at_period_end && (
            <button
              type="button"
              onClick={() => void handleCancel()}
              disabled={cancelMutation.isPending}
              className={cn(dangerButtonClass, 'ml-auto')}
            >
              {cancelMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <XCircle className="w-4 h-4" />
              )}
              {t('settings.cancelSubscription')}
            </button>
          )}
        </div>
      )}

      {/* Invoices */}
      {configured && hasCustomer && (
        <div className="space-y-3">
          <h3 className="font-medium">{t('settings.billingHistory')}</h3>
          <TransactionsTable
            transactions={transactions}
            isLoading={transactionsLoading}
            onError={(message) => setActionError(message)}
          />
        </div>
      )}

      {/* Footnote */}
      <p className="text-[11px] text-muted-foreground border-t border-white/10 pt-3">
        {t('settings.securePaymentsViaPaddle')}
      </p>
    </div>
  );
}

export default PaddleBilling;
