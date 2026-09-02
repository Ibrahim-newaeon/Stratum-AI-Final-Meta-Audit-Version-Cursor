/**
 * Billing (Super Admin View)
 *
 * Platform billing overview - plans, invoices, subscriptions.
 *
 * Paddle Billing is the system of record: transactions, invoices, payment methods and
 * dunning (payment retries) all live in Paddle. This view reads the state mirrored on
 * tenants, shows Paddle identifiers and links out to Paddle-hosted invoice PDFs. It never
 * mutates billing state - there is no "retry payment" or "generate invoice" here.
 */

import { useState } from 'react';
import { cn } from '@/lib/utils';
import {
  useBillingInvoices,
  useBillingPlans,
  useBillingSubscriptions,
  useRevenue,
} from '@/api/hooks';
import { useToast } from '@/components/ui/use-toast';
import {
  ArrowTopRightOnSquareIcon,
  ArrowTrendingUpIcon,
  CheckCircleIcon,
  ClockIcon,
  CurrencyDollarIcon,
  DocumentTextIcon,
  EnvelopeIcon,
  ExclamationTriangleIcon,
  PauseCircleIcon,
  PencilSquareIcon,
  UserGroupIcon,
  XCircleIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';

type PlanType = 'free' | 'starter' | 'professional' | 'enterprise';
type SubscriptionStatus = 'active' | 'trialing' | 'past_due' | 'paused' | 'canceled';
type InvoiceStatus = 'paid' | 'pending' | 'overdue' | 'failed';

interface Subscription {
  id: string;
  tenantId: string;
  tenantName: string;
  plan: PlanType;
  /** Paddle status; null for tenants without a Paddle subscription. */
  status: SubscriptionStatus | null;
  mrr: number;
  startDate: Date | null;
  nextBilling: Date | null;
  cancelAtPeriodEnd: boolean;
  /** Paddle subscription id (sub_...). */
  paddleSubscriptionId: string | null;
  /** Paddle customer id (ctm_...). */
  paddleCustomerId: string | null;
}

interface Invoice {
  id: string;
  tenantName: string;
  amount: number;
  status: InvoiceStatus;
  dueDate: Date | null;
  paidAt: Date | null;
  /** Paddle transaction id (txn_...) backing this invoice. */
  paddleTransactionId: string | null;
  /** Paddle-hosted invoice PDF URL, when available. */
  invoiceUrl: string | null;
}

interface Plan {
  id: string;
  name: string;
  /** Monthly list price; null means custom pricing (enterprise). */
  price: number | null;
  features: string[];
  subscribers: number;
  highlighted?: boolean;
}

const INVOICE_PDF_UNAVAILABLE = 'Invoice PDF is available from Paddle';

const PLAN_TYPES: PlanType[] = ['free', 'starter', 'professional', 'enterprise'];

const toPlanType = (value: string): PlanType =>
  PLAN_TYPES.includes(value as PlanType) ? (value as PlanType) : 'free';

const toDate = (value: string | null | undefined): Date | null => {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

const formatDate = (value: Date | null): string => (value ? value.toLocaleDateString() : '—');

const formatPrice = (price: number | null): string =>
  price === null ? 'Custom' : `$${price.toLocaleString()}`;

const formatStatus = (status: SubscriptionStatus | InvoiceStatus | null): string =>
  status ? status.replace('_', ' ') : 'no subscription';

const daysFromNow = (days: number): Date => new Date(Date.now() + days * 24 * 60 * 60 * 1000);

export default function Billing() {
  const [activeTab, setActiveTab] = useState<'overview' | 'subscriptions' | 'invoices' | 'plans'>(
    'overview'
  );
  const { toast } = useToast();

  // Modal states
  const [selectedInvoice, setSelectedInvoice] = useState<Invoice | null>(null);
  const [selectedSubscription, setSelectedSubscription] = useState<Subscription | null>(null);
  const [selectedPlan, setSelectedPlan] = useState<Plan | null>(null);
  const [showInvoiceModal, setShowInvoiceModal] = useState(false);
  const [showSubscriptionModal, setShowSubscriptionModal] = useState(false);
  const [showEditPlanModal, setShowEditPlanModal] = useState(false);

  // Fetch data from API
  const { data: revenueData } = useRevenue();
  const { data: plansData } = useBillingPlans();
  const { data: invoicesData } = useBillingInvoices();
  const { data: subscriptionsData } = useBillingSubscriptions();

  // Default mock data
  const mockMetrics = {
    mrr: 45890,
    mrrGrowth: 8.5,
    arr: 550680,
    activeSubscriptions: 89,
    churnRate: 2.3,
    pastDue: 4,
    totalRevenue: 1250000,
  };

  // Mock subscriptions carry Paddle identifiers only - payment methods stay in Paddle.
  const mockSubscriptions: Subscription[] = [
    {
      id: 's1',
      tenantId: 't1',
      tenantName: 'Acme Corporation',
      plan: 'enterprise',
      status: 'active',
      mrr: 2499,
      startDate: new Date('2024-01-15'),
      nextBilling: daysFromNow(15),
      cancelAtPeriodEnd: false,
      paddleSubscriptionId: 'sub_01hv2q8b9c0d1e2f3g4h5j6k7m',
      paddleCustomerId: 'ctm_01hv2q7k8m3n4p5r6s7t8u9v0w',
    },
    {
      id: 's2',
      tenantId: 't2',
      tenantName: 'TechStart Inc',
      plan: 'professional',
      status: 'active',
      mrr: 999,
      startDate: new Date('2024-03-01'),
      nextBilling: daysFromNow(5),
      cancelAtPeriodEnd: false,
      paddleSubscriptionId: 'sub_01hv2qb2c3d4e5f6g7h8j9k0m1',
      paddleCustomerId: 'ctm_01hv2qa1b2c3d4e5f6g7h8j9k0',
    },
    {
      id: 's3',
      tenantId: 't3',
      tenantName: 'Fashion Forward',
      plan: 'starter',
      status: 'past_due',
      mrr: 499,
      startDate: new Date('2024-02-15'),
      nextBilling: daysFromNow(-5),
      cancelAtPeriodEnd: false,
      paddleSubscriptionId: 'sub_01hv2qc3d4e5f6g7h8j9k0m1n2',
      paddleCustomerId: 'ctm_01hv2qd4e5f6g7h8j9k0m1n2p3',
    },
    {
      id: 's4',
      tenantId: 't4',
      tenantName: 'HealthPlus',
      plan: 'starter',
      status: 'trialing',
      mrr: 0,
      startDate: daysFromNow(-7),
      nextBilling: daysFromNow(7),
      cancelAtPeriodEnd: false,
      paddleSubscriptionId: 'sub_01hv2qe5f6g7h8j9k0m1n2p3q4',
      paddleCustomerId: 'ctm_01hv2qf6g7h8j9k0m1n2p3q4r5',
    },
    {
      id: 's5',
      tenantId: 't5',
      tenantName: 'Bright Retail',
      plan: 'professional',
      status: 'paused',
      mrr: 0,
      startDate: new Date('2024-05-20'),
      nextBilling: null,
      cancelAtPeriodEnd: false,
      paddleSubscriptionId: 'sub_01hv2qg7h8j9k0m1n2p3q4r5s6',
      paddleCustomerId: 'ctm_01hv2qh8j9k0m1n2p3q4r5s6t7',
    },
  ];

  const mockInvoices: Invoice[] = [
    {
      id: 'inv-001',
      tenantName: 'Acme Corporation',
      amount: 2499,
      status: 'paid',
      dueDate: daysFromNow(-5),
      paidAt: daysFromNow(-6),
      paddleTransactionId: 'txn_01hv2qj9k0m1n2p3q4r5s6t7u8',
      invoiceUrl: null,
    },
    {
      id: 'inv-002',
      tenantName: 'TechStart Inc',
      amount: 999,
      status: 'pending',
      dueDate: daysFromNow(5),
      paidAt: null,
      paddleTransactionId: 'txn_01hv2qk0m1n2p3q4r5s6t7u8v9',
      invoiceUrl: null,
    },
    {
      id: 'inv-003',
      tenantName: 'Fashion Forward',
      amount: 499,
      status: 'overdue',
      dueDate: daysFromNow(-10),
      paidAt: null,
      paddleTransactionId: 'txn_01hv2qm1n2p3q4r5s6t7u8v9w0',
      invoiceUrl: null,
    },
  ];

  // Mirrors core TIER_PRICING (Starter $499, Professional $999, Enterprise custom).
  const mockPlans: Plan[] = [
    {
      id: 'starter',
      name: 'Starter',
      price: 499,
      features: ['Meta (Facebook, Instagram, WhatsApp)', '50 campaigns', 'Basic analytics', 'Email support'],
      subscribers: 23,
    },
    {
      id: 'professional',
      name: 'Professional',
      price: 999,
      features: [
        'Meta (Facebook, Instagram, WhatsApp)',
        'Unlimited campaigns',
        'Advanced analytics',
        'Priority support',
        'Autopilot',
      ],
      subscribers: 52,
      highlighted: true,
    },
    {
      id: 'enterprise',
      name: 'Enterprise',
      price: null,
      features: [
        'Meta (Facebook, Instagram, WhatsApp)',
        'Unlimited campaigns',
        'Custom analytics',
        'Dedicated support',
        'Full Autopilot',
        'SLA',
      ],
      subscribers: 14,
    },
  ];

  // Use API data or fallback to mock
  const subscriptions: Subscription[] =
    subscriptionsData?.items && subscriptionsData.items.length > 0
      ? subscriptionsData.items.map((s) => ({
          id: s.id,
          tenantId: String(s.tenantId),
          tenantName: s.tenantName,
          plan: toPlanType(s.plan),
          status: s.status,
          mrr: s.mrr,
          startDate: toDate(s.startDate),
          nextBilling: toDate(s.nextBillingDate),
          cancelAtPeriodEnd: s.cancelAtPeriodEnd,
          paddleSubscriptionId: s.paddleSubscriptionId ?? null,
          paddleCustomerId: s.paddleCustomerId ?? null,
        }))
      : mockSubscriptions;

  const invoices: Invoice[] =
    invoicesData?.items && invoicesData.items.length > 0
      ? invoicesData.items.map((i) => ({
          id: i.invoiceNumber ?? i.id,
          tenantName: i.tenantName,
          amount: i.amount,
          status: i.status,
          dueDate: toDate(i.dueDate),
          paidAt: toDate(i.paidAt),
          paddleTransactionId: i.paddleTransactionId ?? null,
          invoiceUrl: i.invoiceUrl ?? null,
        }))
      : mockInvoices;

  const plans: Plan[] =
    plansData && plansData.length > 0
      ? plansData
          .filter((p) => (p.tier ?? p.id) !== 'free')
          .map((p) => ({
            id: p.id,
            name: p.name,
            price: p.price,
            features: p.features,
            subscribers: p.subscriberCount,
            highlighted: (p.tier ?? p.id) === 'professional',
          }))
      : mockPlans;

  const metrics = {
    mrr: revenueData?.mrr ?? mockMetrics.mrr,
    mrrGrowth: revenueData?.mrrGrowth ?? mockMetrics.mrrGrowth,
    arr: revenueData?.arr ?? mockMetrics.arr,
    activeSubscriptions: subscriptionsData?.items
      ? subscriptionsData.items.filter((s) => s.status === 'active').length
      : mockMetrics.activeSubscriptions,
    churnRate: revenueData?.churnRate ?? mockMetrics.churnRate,
    pastDue: subscriptionsData?.items
      ? subscriptionsData.items.filter((s) => s.status === 'past_due').length
      : mockMetrics.pastDue,
    totalRevenue: mockMetrics.totalRevenue,
  };

  const pastDueSubscriptions = subscriptions.filter((s) => s.status === 'past_due');
  const pausedSubscriptions = subscriptions.filter((s) => s.status === 'paused');

  // Export billing report to CSV
  const handleExportReport = () => {
    try {
      const csvData = [
        ['Invoice ID', 'Tenant', 'Amount', 'Status', 'Due Date', 'Paid At', 'Paddle Transaction'],
        ...invoices.map((inv) => [
          inv.id,
          inv.tenantName,
          `$${inv.amount}`,
          inv.status,
          formatDate(inv.dueDate),
          inv.paidAt ? formatDate(inv.paidAt) : 'N/A',
          inv.paddleTransactionId ?? '',
        ]),
      ];
      const csvContent = csvData.map((row) => row.join(',')).join('\n');
      const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `billing-report-${new Date().toISOString().split('T')[0]}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      toast({
        title: 'Export Successful',
        description: 'Billing report downloaded as CSV',
      });
    } catch (error) {
      toast({
        title: 'Export Failed',
        description: 'Unable to export billing report',
        variant: 'destructive',
      });
    }
  };

  // Contact customer - open email client
  const handleContactCustomer = (tenantName: string) => {
    // In production, this would fetch actual email from backend
    const email = `billing@${tenantName.toLowerCase().replace(/\s+/g, '')}.com`;
    window.location.href = `mailto:${email}?subject=Payment%20Issue%20-%20${encodeURIComponent(tenantName)}&body=Dear%20${encodeURIComponent(tenantName)}%20Team,%0A%0AWe%20noticed%20there%20is%20an%20issue%20with%20your%20payment.%20Please%20update%20your%20payment%20method%20from%20Settings%20%3E%20Billing.`;
    toast({
      title: 'Email Client Opened',
      description: `Opening email to contact ${tenantName}`,
    });
  };

  // Manage subscription - show modal
  const handleManageSubscription = (subscription: Subscription) => {
    setSelectedSubscription(subscription);
    setShowSubscriptionModal(true);
  };

  // View invoice - show modal
  const handleViewInvoice = (invoice: Invoice) => {
    setSelectedInvoice(invoice);
    setShowInvoiceModal(true);
  };

  // Download invoice - opens the Paddle-hosted invoice PDF when we have its URL
  const handleDownloadInvoice = (invoice: Invoice) => {
    if (!invoice.invoiceUrl) {
      toast({
        title: 'Invoice PDF unavailable',
        description: INVOICE_PDF_UNAVAILABLE,
      });
      return;
    }
    window.open(invoice.invoiceUrl, '_blank', 'noopener,noreferrer');
  };

  // Edit plan - show modal
  const handleEditPlan = (plan: Plan) => {
    setSelectedPlan(plan);
    setShowEditPlanModal(true);
  };

  const getStatusColor = (status: SubscriptionStatus | InvoiceStatus | null) => {
    switch (status) {
      case 'active':
      case 'paid':
        return 'text-success bg-success/10';
      case 'past_due':
      case 'overdue':
        return 'text-danger bg-danger/10';
      case 'pending':
      case 'trialing':
      case 'paused':
        return 'text-warning bg-warning/10';
      case 'canceled':
      case 'failed':
      default:
        return 'text-text-muted bg-surface-tertiary';
    }
  };

  const getStatusIcon = (status: SubscriptionStatus | InvoiceStatus | null) => {
    switch (status) {
      case 'active':
      case 'paid':
        return <CheckCircleIcon className="w-4 h-4" />;
      case 'past_due':
      case 'overdue':
      case 'failed':
        return <ExclamationTriangleIcon className="w-4 h-4" />;
      case 'pending':
      case 'trialing':
        return <ClockIcon className="w-4 h-4" />;
      case 'paused':
        return <PauseCircleIcon className="w-4 h-4" />;
      case 'canceled':
      default:
        return <XCircleIcon className="w-4 h-4" />;
    }
  };

  const tabs = [
    { id: 'overview' as const, label: 'Overview' },
    { id: 'subscriptions' as const, label: 'Subscriptions' },
    { id: 'invoices' as const, label: 'Invoices' },
    { id: 'plans' as const, label: 'Plans' },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Billing</h1>
          <p className="text-text-muted">Revenue and subscription overview · powered by Paddle</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleExportReport}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-secondary border border-white/10 text-text-secondary hover:text-white transition-colors"
          >
            <DocumentTextIcon className="w-4 h-4" />
            Export Report
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-2 border-b border-white/10 pb-4">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              'px-4 py-2 rounded-lg transition-colors',
              activeTab === tab.id
                ? 'bg-stratum-500/10 text-stratum-400'
                : 'text-text-muted hover:text-white hover:bg-white/5'
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Overview Tab */}
      {activeTab === 'overview' && (
        <div className="space-y-6">
          {/* Revenue Metrics */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="p-4 rounded-xl bg-gradient-to-br from-stratum-500/10 to-stratum-600/5 border border-stratum-500/20">
              <div className="flex items-center gap-2 text-text-muted text-sm mb-2">
                <CurrencyDollarIcon className="w-4 h-4" />
                Monthly Recurring Revenue
              </div>
              <div className="text-3xl font-bold text-white">${metrics.mrr.toLocaleString()}</div>
              <div className="flex items-center gap-1 text-success text-sm mt-2">
                <ArrowTrendingUpIcon className="w-4 h-4" />+{metrics.mrrGrowth}% vs last month
              </div>
            </div>

            <div className="p-4 rounded-xl bg-surface-secondary border border-white/10">
              <div className="text-text-muted text-sm mb-2">Annual Recurring Revenue</div>
              <div className="text-3xl font-bold text-white">${metrics.arr.toLocaleString()}</div>
            </div>

            <div className="p-4 rounded-xl bg-surface-secondary border border-white/10">
              <div className="flex items-center gap-2 text-text-muted text-sm mb-2">
                <UserGroupIcon className="w-4 h-4" />
                Active Subscriptions
              </div>
              <div className="text-3xl font-bold text-white">{metrics.activeSubscriptions}</div>
            </div>

            <div className="p-4 rounded-xl bg-surface-secondary border border-white/10">
              <div className="text-text-muted text-sm mb-2">Churn Rate</div>
              <div className="text-3xl font-bold text-warning">{metrics.churnRate}%</div>
              <div className="flex items-center gap-1 text-danger text-sm mt-2">
                <ExclamationTriangleIcon className="w-4 h-4" />
                {metrics.pastDue} past due
              </div>
            </div>
          </div>

          {/* Plan Distribution */}
          <div className="rounded-2xl bg-surface-secondary border border-white/10 p-6">
            <h2 className="font-semibold text-white mb-4">Plan Distribution</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {plans.map((plan) => (
                <div
                  key={plan.id}
                  className={cn(
                    'p-4 rounded-xl border',
                    plan.highlighted
                      ? 'bg-stratum-500/10 border-stratum-500/30'
                      : 'bg-surface-tertiary border-white/5'
                  )}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-medium text-white">{plan.name}</span>
                    <span className="text-stratum-400">
                      {plan.price === null ? 'Custom' : `${formatPrice(plan.price)}/mo`}
                    </span>
                  </div>
                  <div className="text-3xl font-bold text-white">{plan.subscribers}</div>
                  <div className="text-sm text-text-muted">subscribers</div>
                  <div className="mt-2 text-sm text-text-muted">
                    {plan.price === null
                      ? 'Custom MRR'
                      : `$${(plan.price * plan.subscribers).toLocaleString()} MRR`}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Dunning Alerts (state mirrored from Paddle; retries run in Paddle) */}
          <div className="rounded-2xl bg-surface-secondary border border-white/10 p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-semibold text-white">Dunning Alerts</h2>
              <span className="text-xs text-text-muted">Payment retries are handled by Paddle</span>
            </div>
            <div className="space-y-3">
              {pastDueSubscriptions.map((sub) => (
                <div
                  key={sub.id}
                  className="flex items-center justify-between p-3 rounded-lg bg-danger/5 border border-danger/20"
                >
                  <div className="flex items-center gap-3">
                    <ExclamationTriangleIcon className="w-5 h-5 text-danger" />
                    <div>
                      <div className="font-medium text-white">{sub.tenantName}</div>
                      <div className="text-sm text-text-muted">
                        Payment past due · Paddle subscription{' '}
                        <span className="font-mono">{sub.paddleSubscriptionId ?? '—'}</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => handleContactCustomer(sub.tenantName)}
                      className="px-3 py-1 rounded-lg bg-surface-tertiary text-text-secondary hover:text-white text-sm transition-colors"
                    >
                      Contact
                    </button>
                  </div>
                </div>
              ))}
              {pausedSubscriptions.map((sub) => (
                <div
                  key={sub.id}
                  className="flex items-center justify-between p-3 rounded-lg bg-warning/5 border border-warning/20"
                >
                  <div className="flex items-center gap-3">
                    <PauseCircleIcon className="w-5 h-5 text-warning" />
                    <div>
                      <div className="font-medium text-white">{sub.tenantName}</div>
                      <div className="text-sm text-text-muted">
                        Subscription paused · Paddle subscription{' '}
                        <span className="font-mono">{sub.paddleSubscriptionId ?? '—'}</span>
                      </div>
                    </div>
                  </div>
                  <button
                    onClick={() => handleContactCustomer(sub.tenantName)}
                    className="px-3 py-1 rounded-lg bg-surface-tertiary text-text-secondary hover:text-white text-sm transition-colors"
                  >
                    Contact
                  </button>
                </div>
              ))}
              {pastDueSubscriptions.length === 0 && pausedSubscriptions.length === 0 && (
                <div className="flex items-center gap-2 text-success p-3">
                  <CheckCircleIcon className="w-5 h-5" />
                  No dunning alerts - all payments up to date
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Subscriptions Tab */}
      {activeTab === 'subscriptions' && (
        <div className="rounded-2xl bg-surface-secondary border border-white/10 overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-white/10">
                <th className="text-left p-4 text-text-muted font-medium">Tenant</th>
                <th className="text-left p-4 text-text-muted font-medium">Plan</th>
                <th className="text-left p-4 text-text-muted font-medium">Status</th>
                <th className="text-left p-4 text-text-muted font-medium">MRR</th>
                <th className="text-left p-4 text-text-muted font-medium">Next Billing</th>
                <th className="text-left p-4 text-text-muted font-medium">Paddle Subscription</th>
                <th className="text-left p-4 text-text-muted font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {subscriptions.map((sub) => (
                <tr key={sub.id} className="hover:bg-white/5 transition-colors">
                  <td className="p-4">
                    <span className="font-medium text-white">{sub.tenantName}</span>
                  </td>
                  <td className="p-4">
                    <span className="px-2 py-1 rounded bg-stratum-500/10 text-stratum-400 text-sm capitalize">
                      {sub.plan}
                    </span>
                  </td>
                  <td className="p-4">
                    <span
                      className={cn(
                        'flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium w-fit',
                        getStatusColor(sub.status)
                      )}
                    >
                      {getStatusIcon(sub.status)}
                      {formatStatus(sub.status)}
                    </span>
                    {sub.cancelAtPeriodEnd && (
                      <div className="text-[11px] text-text-muted mt-1">Cancels at period end</div>
                    )}
                  </td>
                  <td className="p-4 text-white font-medium">${sub.mrr.toLocaleString()}</td>
                  <td className="p-4 text-text-muted">{formatDate(sub.nextBilling)}</td>
                  <td className="p-4">
                    <span className="font-mono text-xs text-text-muted">
                      {sub.paddleSubscriptionId ?? '—'}
                    </span>
                  </td>
                  <td className="p-4">
                    <button
                      onClick={() => handleManageSubscription(sub)}
                      className="text-stratum-400 hover:text-stratum-300 text-sm"
                    >
                      Manage
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Invoices Tab */}
      {activeTab === 'invoices' && (
        <div className="rounded-2xl bg-surface-secondary border border-white/10 overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-white/10">
                <th className="text-left p-4 text-text-muted font-medium">Invoice ID</th>
                <th className="text-left p-4 text-text-muted font-medium">Tenant</th>
                <th className="text-left p-4 text-text-muted font-medium">Amount</th>
                <th className="text-left p-4 text-text-muted font-medium">Status</th>
                <th className="text-left p-4 text-text-muted font-medium">Due Date</th>
                <th className="text-left p-4 text-text-muted font-medium">Paddle Transaction</th>
                <th className="text-left p-4 text-text-muted font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {invoices.map((invoice) => (
                <tr key={invoice.id} className="hover:bg-white/5 transition-colors">
                  <td className="p-4 font-mono text-stratum-400">{invoice.id}</td>
                  <td className="p-4 text-white">{invoice.tenantName}</td>
                  <td className="p-4 text-white font-medium">${invoice.amount.toLocaleString()}</td>
                  <td className="p-4">
                    <span
                      className={cn(
                        'flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium w-fit',
                        getStatusColor(invoice.status)
                      )}
                    >
                      {getStatusIcon(invoice.status)}
                      {invoice.status}
                    </span>
                  </td>
                  <td className="p-4 text-text-muted">{formatDate(invoice.dueDate)}</td>
                  <td className="p-4">
                    <span className="font-mono text-xs text-text-muted">
                      {invoice.paddleTransactionId ?? '—'}
                    </span>
                  </td>
                  <td className="p-4">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleViewInvoice(invoice)}
                        className="text-stratum-400 hover:text-stratum-300 text-sm"
                      >
                        View
                      </button>
                      <button
                        onClick={() => handleDownloadInvoice(invoice)}
                        disabled={!invoice.invoiceUrl}
                        title={invoice.invoiceUrl ? 'Open invoice PDF' : INVOICE_PDF_UNAVAILABLE}
                        className="text-text-muted hover:text-white text-sm disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:text-text-muted"
                      >
                        Download
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Plans Tab */}
      {activeTab === 'plans' && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {plans.map((plan) => (
            <div
              key={plan.id}
              className={cn(
                'rounded-2xl border p-6',
                plan.highlighted
                  ? 'bg-gradient-to-br from-stratum-500/10 to-stratum-600/5 border-stratum-500/30'
                  : 'bg-surface-secondary border-white/10'
              )}
            >
              <h3 className="text-xl font-semibold text-white mb-2">{plan.name}</h3>
              <div className="flex items-baseline gap-1 mb-4">
                <span className="text-3xl font-bold text-white">{formatPrice(plan.price)}</span>
                {plan.price !== null && <span className="text-text-muted">/month</span>}
              </div>

              <div className="py-4 border-t border-b border-white/10 mb-4">
                <div className="text-lg font-semibold text-white">{plan.subscribers}</div>
                <div className="text-sm text-text-muted">active subscribers</div>
              </div>

              <ul className="space-y-2">
                {plan.features.map((feature, i) => (
                  <li key={i} className="flex items-center gap-2 text-sm text-text-secondary">
                    <CheckCircleIcon className="w-4 h-4 text-success" />
                    {feature}
                  </li>
                ))}
              </ul>

              <button
                onClick={() => handleEditPlan(plan)}
                className="w-full mt-6 py-2 rounded-lg bg-surface-tertiary text-text-secondary hover:text-white transition-colors"
              >
                Edit Plan
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Invoice Detail Modal */}
      {showInvoiceModal && selectedInvoice && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-surface-secondary rounded-2xl border border-white/10 p-6 w-full max-w-md mx-4">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-semibold text-white">Invoice Details</h2>
              <button
                onClick={() => {
                  setShowInvoiceModal(false);
                  setSelectedInvoice(null);
                }}
                className="text-text-muted hover:text-white"
              >
                <XMarkIcon className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-4">
              <div className="flex justify-between">
                <span className="text-text-muted">Invoice ID</span>
                <span className="text-white font-mono">{selectedInvoice.id}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Tenant</span>
                <span className="text-white">{selectedInvoice.tenantName}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Amount</span>
                <span className="text-white font-semibold">
                  ${selectedInvoice.amount.toLocaleString()}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Status</span>
                <span
                  className={cn(
                    'px-2 py-1 rounded-full text-xs font-medium',
                    getStatusColor(selectedInvoice.status)
                  )}
                >
                  {selectedInvoice.status}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Due Date</span>
                <span className="text-white">{formatDate(selectedInvoice.dueDate)}</span>
              </div>
              {selectedInvoice.paidAt && (
                <div className="flex justify-between">
                  <span className="text-text-muted">Paid At</span>
                  <span className="text-white">{formatDate(selectedInvoice.paidAt)}</span>
                </div>
              )}
              <div className="flex justify-between gap-4">
                <span className="text-text-muted">Paddle Transaction</span>
                <span className="text-white font-mono text-xs break-all text-right">
                  {selectedInvoice.paddleTransactionId ?? '—'}
                </span>
              </div>
            </div>

            <div className="flex gap-3 mt-6">
              <button
                onClick={() => handleDownloadInvoice(selectedInvoice)}
                disabled={!selectedInvoice.invoiceUrl}
                title={selectedInvoice.invoiceUrl ? 'Open invoice PDF' : INVOICE_PDF_UNAVAILABLE}
                className="flex-1 flex items-center justify-center gap-2 py-2 rounded-lg bg-stratum-500 text-white hover:bg-stratum-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <ArrowTopRightOnSquareIcon className="w-4 h-4" />
                Download
              </button>
              <button
                onClick={() => {
                  setShowInvoiceModal(false);
                  setSelectedInvoice(null);
                }}
                className="flex-1 py-2 rounded-lg bg-surface-tertiary text-text-secondary hover:text-white transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Subscription Management Modal */}
      {showSubscriptionModal && selectedSubscription && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-surface-secondary rounded-2xl border border-white/10 p-6 w-full max-w-md mx-4">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-semibold text-white">Subscription</h2>
              <button
                onClick={() => {
                  setShowSubscriptionModal(false);
                  setSelectedSubscription(null);
                }}
                className="text-text-muted hover:text-white"
              >
                <XMarkIcon className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-4">
              <div className="flex justify-between">
                <span className="text-text-muted">Tenant</span>
                <span className="text-white font-semibold">{selectedSubscription.tenantName}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Plan</span>
                <span className="px-2 py-1 rounded bg-stratum-500/10 text-stratum-400 text-sm capitalize">
                  {selectedSubscription.plan}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Status</span>
                <span
                  className={cn(
                    'px-2 py-1 rounded-full text-xs font-medium',
                    getStatusColor(selectedSubscription.status)
                  )}
                >
                  {formatStatus(selectedSubscription.status)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">MRR</span>
                <span className="text-white font-semibold">
                  ${selectedSubscription.mrr.toLocaleString()}
                </span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-text-muted">Paddle Subscription</span>
                <span className="text-white font-mono text-xs break-all text-right">
                  {selectedSubscription.paddleSubscriptionId ?? '—'}
                </span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-text-muted">Paddle Customer</span>
                <span className="text-white font-mono text-xs break-all text-right">
                  {selectedSubscription.paddleCustomerId ?? '—'}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Next Billing</span>
                <span className="text-white">{formatDate(selectedSubscription.nextBilling)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Start Date</span>
                <span className="text-white">{formatDate(selectedSubscription.startDate)}</span>
              </div>
              {selectedSubscription.cancelAtPeriodEnd && (
                <div className="flex justify-between">
                  <span className="text-text-muted">Scheduled change</span>
                  <span className="text-warning font-semibold">Cancels at period end</span>
                </div>
              )}
              <p className="text-xs text-text-muted pt-2 border-t border-white/10">
                Plan changes, cancellations and payment retries are managed in Paddle and mirrored
                here through webhooks.
              </p>
            </div>

            <div className="flex gap-3 mt-6">
              <button
                onClick={() => handleContactCustomer(selectedSubscription.tenantName)}
                className="flex-1 flex items-center justify-center gap-2 py-2 rounded-lg bg-surface-tertiary text-text-secondary hover:text-white transition-colors"
              >
                <EnvelopeIcon className="w-4 h-4" />
                Contact
              </button>
              <button
                onClick={() => {
                  setShowSubscriptionModal(false);
                  setSelectedSubscription(null);
                }}
                className="flex-1 py-2 rounded-lg bg-stratum-500 text-white hover:bg-stratum-600 transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Plan Modal */}
      {showEditPlanModal && selectedPlan && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-surface-secondary rounded-2xl border border-white/10 p-6 w-full max-w-md mx-4">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-semibold text-white">Edit Plan: {selectedPlan.name}</h2>
              <button
                onClick={() => {
                  setShowEditPlanModal(false);
                  setSelectedPlan(null);
                }}
                className="text-text-muted hover:text-white"
              >
                <XMarkIcon className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-sm text-text-muted mb-2">Plan Name</label>
                <input
                  type="text"
                  defaultValue={selectedPlan.name}
                  className="w-full px-4 py-2 rounded-lg bg-surface-tertiary border border-white/10 text-white focus:outline-none focus:ring-2 focus:ring-stratum-500/50"
                />
              </div>
              <div>
                <label className="block text-sm text-text-muted mb-2">List Price ($/month)</label>
                <input
                  type="text"
                  value={formatPrice(selectedPlan.price)}
                  readOnly
                  className="w-full px-4 py-2 rounded-lg bg-surface-tertiary border border-white/10 text-text-muted focus:outline-none"
                />
                <p className="text-xs text-text-muted mt-1">
                  The billable price is set on the Paddle price (pri_...) for this tier.
                </p>
              </div>
              <div>
                <label className="block text-sm text-text-muted mb-2">
                  Features (one per line)
                </label>
                <textarea
                  defaultValue={selectedPlan.features.join('\n')}
                  rows={4}
                  className="w-full px-4 py-2 rounded-lg bg-surface-tertiary border border-white/10 text-white focus:outline-none focus:ring-2 focus:ring-stratum-500/50 resize-none"
                />
              </div>
              <div className="pt-2 border-t border-white/10">
                <div className="text-sm text-text-muted">
                  Current Subscribers:{' '}
                  <span className="text-white font-semibold">{selectedPlan.subscribers}</span>
                </div>
              </div>
            </div>

            <div className="flex gap-3 mt-6">
              <button
                onClick={() => {
                  toast({
                    title: 'Plan Updated',
                    description: `${selectedPlan.name} plan has been updated successfully`,
                  });
                  setShowEditPlanModal(false);
                  setSelectedPlan(null);
                }}
                className="flex-1 flex items-center justify-center gap-2 py-2 rounded-lg bg-stratum-500 text-white hover:bg-stratum-600 transition-colors"
              >
                <PencilSquareIcon className="w-4 h-4" />
                Save Changes
              </button>
              <button
                onClick={() => {
                  setShowEditPlanModal(false);
                  setSelectedPlan(null);
                }}
                className="flex-1 py-2 rounded-lg bg-surface-tertiary text-text-secondary hover:text-white transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
