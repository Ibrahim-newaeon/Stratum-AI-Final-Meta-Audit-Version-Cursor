/**
 * Billing checkout success page (/dashboard/billing/success)
 *
 * Paddle redirects here (the checkout `successUrl`) after a completed overlay
 * checkout. Activation is asynchronous: the backend learns about the new
 * subscription through the signed Paddle webhook, so this page invalidates the
 * billing queries and polls the subscription for a short while.
 */

import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, CheckCircle2, Loader2 } from 'lucide-react';
import { billingQueryKeys, useBillingSubscription } from '@/api/billing';

const BILLING_SETTINGS_PATH = '/dashboard/settings?tab=billing';

export function BillingSuccess() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const { data: subscription } = useBillingSubscription({ refetchInterval: 3000 });
  const activated = !!subscription?.has_subscription;

  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: billingQueryKeys.all });
  }, [queryClient]);

  return (
    <div className="max-w-xl mx-auto py-12">
      <div className="p-8 rounded-2xl border border-white/10 glass card-3d text-center space-y-4">
        <div className="mx-auto w-14 h-14 rounded-full bg-green-500/15 border border-green-500/30 flex items-center justify-center">
          <CheckCircle2 className="w-7 h-7 text-green-400" />
        </div>
        <h1 className="text-xl font-semibold">{t('settings.checkoutSuccess')}</h1>
        <p className="text-sm text-muted-foreground">{t('settings.checkoutSuccessDesc')}</p>

        <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground">
          {activated ? (
            <>
              <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />
              <span>
                {t('settings.subscriptionStatus')}: {t('settings.statusActive')}
              </span>
            </>
          ) : (
            <>
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              <span>{t('settings.subscriptionStatus')}…</span>
            </>
          )}
        </div>

        <Link
          to={BILLING_SETTINGS_PATH}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 text-sm"
        >
          <ArrowLeft className="w-4 h-4" />
          {t('settings.backToBilling')}
        </Link>
      </div>
    </div>
  );
}

export default BillingSuccess;
