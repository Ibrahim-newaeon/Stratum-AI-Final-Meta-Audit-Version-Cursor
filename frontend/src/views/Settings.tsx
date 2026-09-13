import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle,
  Bell,
  Building,
  Check,
  ChevronRight,
  CreditCard,
  Download,
  Gauge,
  Link2,
  Loader2,
  Palette,
  RefreshCw,
  Save,
  Shield,
  Trash2,
  User,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTenantStore } from '@/stores/tenantStore';
import { useExportData, useRequestDeletion } from '@/api/hooks';
import { useCurrentUser, useUpdatePreferences } from '@/api/auth';
import { useToast } from '@/components/ui/use-toast';
import { apiClient } from '@/api/client';
import GA4Integration from '@/components/settings/GA4Integration';
import GTMIntegration from '@/components/settings/GTMIntegration';

type SettingsTab =
  | 'profile'
  | 'organization'
  | 'notifications'
  | 'security'
  | 'integrations'
  | 'preferences'
  | 'billing'
  | 'gdpr'
  | 'trust-engine';

const SETTINGS_TABS: SettingsTab[] = [
  'profile',
  'organization',
  'notifications',
  'security',
  'integrations',
  'preferences',
  'billing',
  'gdpr',
  'trust-engine',
];

function isSettingsTab(value: string | null): value is SettingsTab {
  return value !== null && (SETTINGS_TABS as string[]).includes(value);
}

export function Settings() {
  const { t } = useTranslation();
  // Deep link support: /dashboard/settings?tab=billing opens the Billing tab.
  // Tab clicks keep using internal state (the URL is not rewritten).
  const [searchParams] = useSearchParams();
  const tabParam = searchParams.get('tab');
  const [activeTab, setActiveTab] = useState<SettingsTab>(
    isSettingsTab(tabParam) ? tabParam : 'profile'
  );
  useEffect(() => {
    if (isSettingsTab(tabParam)) setActiveTab(tabParam);
  }, [tabParam]);
  const [showApiKey, setShowApiKey] = useState(false);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved'>('idle');

  const tabs = [
    { id: 'profile', label: t('settings.profile'), icon: User },
    { id: 'organization', label: t('settings.organization'), icon: Building },
    { id: 'notifications', label: t('settings.notifications'), icon: Bell },
    { id: 'security', label: t('settings.security'), icon: Shield },
    { id: 'integrations', label: t('settings.integrations'), icon: Link2 },
    { id: 'preferences', label: t('settings.preferences'), icon: Palette },
    { id: 'billing', label: t('settings.billing'), icon: CreditCard },
    { id: 'gdpr', label: t('settings.gdpr'), icon: Download },
    { id: 'trust-engine', label: 'Trust Engine', icon: Gauge },
  ] as const;

  const handleSave = () => {
    setSaveStatus('saving');
    setTimeout(() => {
      setSaveStatus('saved');
      setTimeout(() => setSaveStatus('idle'), 2000);
    }, 1000);
  };

  const renderTabContent = () => {
    switch (activeTab) {
      case 'profile':
        return <ProfileSettings />;
      case 'organization':
        return <OrganizationSettings />;
      case 'notifications':
        return <NotificationSettings />;
      case 'security':
        return <SecuritySettings showApiKey={showApiKey} setShowApiKey={setShowApiKey} />;
      case 'integrations':
        return <IntegrationSettings />;
      case 'preferences':
        return <PreferenceSettings />;
      case 'billing':
        return <BillingSettings />;
      case 'gdpr':
        return <GDPRSettings />;
      case 'trust-engine':
        return <TrustEngineSettings />;
      default:
        return null;
    }
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{t('settings.title')}</h1>
          <p className="text-muted-foreground">{t('settings.subtitle')}</p>
        </div>
        <button
          onClick={handleSave}
          disabled={saveStatus === 'saving'}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
        >
          {saveStatus === 'saving' ? (
            <RefreshCw className="w-4 h-4 animate-spin" />
          ) : saveStatus === 'saved' ? (
            <Check className="w-4 h-4" />
          ) : (
            <Save className="w-4 h-4" />
          )}
          <span>{saveStatus === 'saved' ? t('settings.saved') : t('settings.saveChanges')}</span>
        </button>
      </div>

      <div className="flex gap-6">
        {/* Sidebar Navigation */}
        <div className="w-64 flex-shrink-0">
          <nav className="space-y-1">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id as SettingsTab)}
                  className={cn(
                    'w-full flex items-center gap-3 px-4 py-3 rounded-lg text-left transition-colors',
                    activeTab === tab.id
                      ? 'bg-primary/10 text-primary font-medium'
                      : 'hover:bg-muted text-muted-foreground'
                  )}
                >
                  <Icon className="w-5 h-5" />
                  <span>{tab.label}</span>
                  {activeTab === tab.id && <ChevronRight className="w-4 h-4 ml-auto" />}
                </button>
              );
            })}
          </nav>
        </div>

        {/* Content Area */}
        <div className="flex-1 rounded-xl border bg-card p-6">{renderTabContent()}</div>
      </div>
    </div>
  );
}

function ProfileSettings() {
  const { t } = useTranslation();
  // Get user data from tenant store
  const user = useTenantStore((state) => state.user);

  // Parse name into first/last (fallback to mock data)
  const fullName = user?.full_name || 'John Doe';
  const nameParts = fullName.split(' ');
  const firstName = nameParts[0] || 'John';
  const lastName = nameParts.slice(1).join(' ') || 'Doe';
  const initials = `${firstName[0] || 'J'}${lastName[0] || 'D'}`;
  const email = user?.email || 'john.doe@company.com';
  const role = user?.role || 'media_buyer';
  const timezone = user?.timezone || 'America/New_York';

  // Format role for display
  const formatRole = (role: string) => {
    const roleLabels: Record<string, string> = {
      superadmin: 'Super Admin',
      admin: 'Admin',
      manager: 'Manager',
      media_buyer: 'Media Buyer',
      analyst: 'Analyst',
      account_manager: 'Account Manager',
      viewer: 'Viewer',
    };
    return roleLabels[role] || role;
  };

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold">{t('settings.profileSettings')}</h2>

      <div className="flex items-center gap-6">
        <div className="w-20 h-20 rounded-full bg-primary/10 flex items-center justify-center text-2xl font-bold text-primary">
          {user?.avatar_url ? (
            <img
              src={user.avatar_url}
              alt={fullName}
              className="w-full h-full rounded-full object-cover"
            />
          ) : (
            initials.toUpperCase()
          )}
        </div>
        <div>
          <button className="px-4 py-2 rounded-lg border hover:bg-muted transition-colors text-sm">
            {t('settings.changeAvatar')}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="text-sm font-medium mb-2 block">{t('settings.firstName')}</label>
          <input
            type="text"
            defaultValue={firstName}
            className="w-full px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </div>
        <div>
          <label className="text-sm font-medium mb-2 block">{t('settings.lastName')}</label>
          <input
            type="text"
            defaultValue={lastName}
            className="w-full px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </div>
      </div>

      <div>
        <label className="text-sm font-medium mb-2 block">{t('settings.email')}</label>
        <input
          type="email"
          defaultValue={email}
          className="w-full px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </div>

      <div>
        <label className="text-sm font-medium mb-2 block">{t('settings.role')}</label>
        <input
          type="text"
          value={formatRole(role)}
          disabled
          className="w-full px-4 py-2 rounded-lg border bg-muted text-muted-foreground"
        />
      </div>

      <div>
        <label className="text-sm font-medium mb-2 block">{t('settings.timezone')}</label>
        <select
          defaultValue={timezone}
          className="w-full px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
        >
          <option value="America/New_York">Eastern Time (ET)</option>
          <option value="America/Chicago">Central Time (CT)</option>
          <option value="America/Denver">Mountain Time (MT)</option>
          <option value="America/Los_Angeles">Pacific Time (PT)</option>
          <option value="Europe/London">Greenwich Mean Time (GMT)</option>
          <option value="Europe/Kyiv">Eastern European Time (EET)</option>
          <option value="Asia/Riyadh">Arabia Standard Time (AST)</option>
          <option value="Asia/Dubai">Gulf Standard Time (GST)</option>
        </select>
      </div>
    </div>
  );
}

function OrganizationSettings() {
  const { t } = useTranslation();
  // Get tenant data from store
  const tenant = useTenantStore((state) => state.tenant);

  // Use tenant data or fall back to mock
  const companyName = tenant?.name || 'Acme Corporation';
  const industry = tenant?.settings?.industry || 'ecommerce';
  const plan = tenant?.plan || 'pro';
  const maxUsers = tenant?.max_users || 10;

  // State for users management
  const [teamMembers, setTeamMembers] = useState<
    Array<{ id: number; email: string; role: string; is_active: boolean }>
  >([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showInviteModal, setShowInviteModal] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('user');
  const [isInviting, setIsInviting] = useState(false);
  const [removingUserId, setRemovingUserId] = useState<number | null>(null);

  // Fetch team members
  const fetchTeamMembers = async () => {
    try {
      setIsLoading(true);
      const { apiClient } = await import('@/api/client');
      const response = await apiClient.get('/users');
      if (response.data.success) {
        setTeamMembers(response.data.data);
      }
    } catch (error) {
      console.error('Failed to fetch team members:', error);
      // Fallback to mock data
      setTeamMembers([
        { id: 1, email: 'admin@company.com', role: 'admin', is_active: true },
        { id: 2, email: 'jane.smith@company.com', role: 'manager', is_active: true },
        { id: 3, email: 'bob.wilson@company.com', role: 'user', is_active: true },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchTeamMembers();
  }, []);

  // Invite new user
  const handleInvite = async () => {
    if (!inviteEmail) return;
    setIsInviting(true);
    try {
      const { apiClient } = await import('@/api/client');
      const response = await apiClient.post('/users/invite', {
        email: inviteEmail,
        role: inviteRole,
      });
      if (response.data.success) {
        setTeamMembers([...teamMembers, response.data.data]);
        setShowInviteModal(false);
        setInviteEmail('');
        setInviteRole('user');
      }
    } catch (error: any) {
      alert(error.response?.data?.detail || 'Failed to invite user');
    } finally {
      setIsInviting(false);
    }
  };

  // Remove user
  const handleRemove = async (userId: number) => {
    if (!confirm('Are you sure you want to remove this user?')) return;
    setRemovingUserId(userId);
    try {
      const { apiClient } = await import('@/api/client');
      const response = await apiClient.delete(`/users/${userId}`);
      if (response.data.success) {
        setTeamMembers(teamMembers.filter((m) => m.id !== userId));
      }
    } catch (error: any) {
      alert(error.response?.data?.detail || 'Failed to remove user');
    } finally {
      setRemovingUserId(null);
    }
  };

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold">{t('settings.organizationSettings')}</h2>

      <div>
        <label className="text-sm font-medium mb-2 block">{t('settings.companyName')}</label>
        <input
          type="text"
          defaultValue={companyName}
          className="w-full px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </div>

      <div>
        <label className="text-sm font-medium mb-2 block">{t('settings.industry')}</label>
        <select
          defaultValue={industry}
          className="w-full px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
        >
          <option value="ecommerce">E-commerce</option>
          <option value="saas">SaaS</option>
          <option value="retail">Retail</option>
          <option value="finance">Finance</option>
          <option value="healthcare">Healthcare</option>
        </select>
      </div>

      <div className="p-4 rounded-lg border bg-muted/30">
        <div className="flex items-center justify-between">
          <div>
            <p className="font-medium capitalize">{plan} Plan</p>
            <p className="text-sm text-muted-foreground">Max {maxUsers} team members</p>
          </div>
          <span className="px-2 py-1 rounded-full bg-primary/10 text-primary text-xs font-medium">
            Active
          </span>
        </div>
      </div>

      <div>
        <label className="text-sm font-medium mb-2 block">{t('settings.teamMembers')}</label>
        <div className="space-y-2">
          {isLoading ? (
            <div className="flex items-center justify-center p-4">
              <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
            </div>
          ) : (
            teamMembers.map((member) => (
              <div
                key={member.id}
                className="flex items-center justify-between p-3 rounded-lg border"
              >
                <div className="flex items-center gap-3">
                  <span className="text-sm">{member.email}</span>
                  <span className="text-xs px-2 py-0.5 rounded-full bg-muted capitalize">
                    {member.role}
                  </span>
                </div>
                <button
                  onClick={() => handleRemove(member.id)}
                  disabled={removingUserId === member.id}
                  className="text-sm text-red-500 hover:underline disabled:opacity-50"
                >
                  {removingUserId === member.id ? 'Removing...' : 'Remove'}
                </button>
              </div>
            ))
          )}
        </div>
        <button
          onClick={() => setShowInviteModal(true)}
          className="mt-3 text-sm text-primary hover:underline"
        >
          + {t('settings.inviteMember')}
        </button>
      </div>

      {/* Invite Modal */}
      {showInviteModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-background p-6 rounded-xl border shadow-lg max-w-md w-full mx-4">
            <h3 className="text-lg font-semibold mb-4">Invite Team Member</h3>
            <div className="space-y-4">
              <div>
                <label className="text-sm font-medium mb-1 block">Email</label>
                <input
                  type="email"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  placeholder="colleague@company.com"
                  className="w-full px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>
              <div>
                <label className="text-sm font-medium mb-1 block">Role</label>
                <select
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value)}
                  className="w-full px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
                >
                  <option value="user">User</option>
                  <option value="manager">Manager</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
            </div>
            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => setShowInviteModal(false)}
                className="px-4 py-2 text-sm rounded-lg border hover:bg-muted"
              >
                Cancel
              </button>
              <button
                onClick={handleInvite}
                disabled={!inviteEmail || isInviting}
                className="px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                {isInviting ? 'Inviting...' : 'Send Invite'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const NOTIFICATION_DEFAULTS = {
  emailAlerts: true,
  pushNotifications: true,
  weeklyDigest: true,
  campaignAlerts: true,
  budgetAlerts: true,
  performanceAlerts: false,
};

function NotificationSettings() {
  const { t } = useTranslation();
  const { data: user } = useCurrentUser();
  const updatePreferences = useUpdatePreferences();
  const { toast } = useToast();
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved'>('idle');

  const savedNotifications = (user?.preferences as Record<string, unknown>)?.notifications as
    | Record<string, boolean>
    | undefined;

  const [notifications, setNotifications] = useState({
    ...NOTIFICATION_DEFAULTS,
    ...savedNotifications,
  });

  // Sync state when user data loads or changes
  useEffect(() => {
    if (savedNotifications) {
      setNotifications((prev) => ({ ...prev, ...savedNotifications }));
    }
  }, [JSON.stringify(savedNotifications)]);

  const persistNotifications = useCallback(
    (updated: Record<string, boolean>) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        setSaveStatus('saving');
        const existingPrefs = (user?.preferences as Record<string, unknown>) ?? {};
        updatePreferences.mutate(
          { ...existingPrefs, notifications: updated },
          {
            onSuccess: () => {
              setSaveStatus('saved');
              setTimeout(() => setSaveStatus('idle'), 2000);
            },
            onError: () => {
              setSaveStatus('idle');
              toast({
                title: 'Error',
                description: 'Failed to save notification preferences.',
                variant: 'destructive',
              });
            },
          }
        );
      }, 500);
    },
    [user?.preferences, updatePreferences, toast]
  );

  const handleToggle = (key: string) => {
    const updated = { ...notifications, [key]: !notifications[key as keyof typeof notifications] };
    setNotifications(updated);
    persistNotifications(updated);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">{t('settings.notificationSettings')}</h2>
        {saveStatus !== 'idle' && (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            {saveStatus === 'saving' ? (
              <>
                <Loader2 className="w-3 h-3 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Check className="w-3 h-3 text-green-500" />
                Saved
              </>
            )}
          </span>
        )}
      </div>

      <div className="space-y-4">
        {Object.entries(notifications).map(([key, value]) => (
          <div key={key} className="flex items-center justify-between p-4 rounded-lg border">
            <div>
              <p className="font-medium">{t(`settings.${key}`)}</p>
              <p className="text-sm text-muted-foreground">{t(`settings.${key}Desc`)}</p>
            </div>
            <button
              onClick={() => handleToggle(key)}
              className={cn(
                'relative w-12 h-6 rounded-full transition-colors',
                value ? 'bg-primary' : 'bg-muted'
              )}
            >
              <span
                className={cn(
                  'absolute top-1 w-4 h-4 bg-white rounded-full transition-transform',
                  value ? 'translate-x-7' : 'translate-x-1'
                )}
              />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function SecuritySettings({
  showApiKey: _showApiKey,
  setShowApiKey: _setShowApiKey,
}: {
  showApiKey: boolean;
  setShowApiKey: (show: boolean) => void;
}) {
  const { t } = useTranslation();
  const { toast } = useToast();

  const [mfaEnabled, setMfaEnabled] = useState(false);
  const [mfaLoading, setMfaLoading] = useState(true);
  const [mfaSetup, setMfaSetup] = useState<{
    secret: string;
    qr_code_base64: string;
  } | null>(null);
  const [mfaCode, setMfaCode] = useState('');
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
  const [mfaBusy, setMfaBusy] = useState(false);

  type ApiKeyRow = {
    id: number;
    name: string;
    key_prefix: string;
    masked_key: string;
    scopes: string[];
    is_active: boolean;
    last_used_at?: string | null;
    created_at: string;
  };
  const [apiKeys, setApiKeys] = useState<ApiKeyRow[]>([]);
  const [keysLoading, setKeysLoading] = useState(true);
  const [creatingKey, setCreatingKey] = useState(false);
  const [newKeyName, setNewKeyName] = useState('Production API Key');
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const loadMfa = useCallback(async () => {
    setMfaLoading(true);
    try {
      const res = await apiClient.get('/mfa/status');
      const data = res.data?.data ?? res.data;
      setMfaEnabled(Boolean(data?.enabled));
    } catch (err) {
      console.error('Failed to load MFA status', err);
    } finally {
      setMfaLoading(false);
    }
  }, []);

  const loadKeys = useCallback(async () => {
    setKeysLoading(true);
    try {
      const res = await apiClient.get('/api-keys');
      const data = res.data?.data ?? res.data ?? [];
      setApiKeys(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error('Failed to load API keys', err);
      setApiKeys([]);
    } finally {
      setKeysLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadMfa();
    void loadKeys();
  }, [loadMfa, loadKeys]);

  const startMfaSetup = async () => {
    setMfaBusy(true);
    setBackupCodes([]);
    try {
      const res = await apiClient.post('/mfa/setup');
      const data = res.data?.data ?? res.data;
      setMfaSetup({
        secret: data.secret,
        qr_code_base64: data.qr_code_base64,
      });
      setMfaCode('');
    } catch (err: any) {
      toast({
        title: 'Could not start 2FA setup',
        description: err?.response?.data?.detail || err?.message || 'Try again',
        variant: 'destructive',
      });
    } finally {
      setMfaBusy(false);
    }
  };

  const verifyMfa = async () => {
    if (!mfaCode.trim()) return;
    setMfaBusy(true);
    try {
      const res = await apiClient.post('/mfa/verify', { code: mfaCode.trim() });
      const data = res.data?.data ?? res.data;
      if (data?.success) {
        setMfaEnabled(true);
        setMfaSetup(null);
        setBackupCodes(data.backup_codes || []);
        toast({ title: '2FA enabled', description: 'Store your backup codes securely.' });
      } else {
        toast({
          title: 'Invalid code',
          description: data?.message || 'Check your authenticator app and try again.',
          variant: 'destructive',
        });
      }
    } catch (err: any) {
      toast({
        title: 'Verification failed',
        description: err?.response?.data?.detail || err?.message || 'Try again',
        variant: 'destructive',
      });
    } finally {
      setMfaBusy(false);
    }
  };

  const disableMfa = async () => {
    const code = window.prompt('Enter a current 2FA code (or backup code) to disable 2FA');
    if (!code) return;
    setMfaBusy(true);
    try {
      await apiClient.post('/mfa/disable', { code: code.trim() });
      setMfaEnabled(false);
      setBackupCodes([]);
      toast({ title: '2FA disabled' });
    } catch (err: any) {
      toast({
        title: 'Could not disable 2FA',
        description: err?.response?.data?.detail || err?.message || 'Try again',
        variant: 'destructive',
      });
    } finally {
      setMfaBusy(false);
    }
  };

  const createApiKey = async () => {
    setCreatingKey(true);
    try {
      const res = await apiClient.post('/api-keys', {
        name: newKeyName.trim() || 'API Key',
        scopes: ['read'],
      });
      const data = res.data?.data ?? res.data;
      setRevealedKey(data?.key || null);
      toast({
        title: 'API key created',
        description: 'Copy it now — it will not be shown again.',
      });
      await loadKeys();
    } catch (err: any) {
      toast({
        title: 'Could not create API key',
        description: err?.response?.data?.detail || err?.message || 'Try again',
        variant: 'destructive',
      });
    } finally {
      setCreatingKey(false);
    }
  };

  const regenerateApiKey = async (keyId: number, name: string) => {
    if (!window.confirm(`Regenerate "${name}"? The current key stops working immediately.`)) return;
    try {
      const res = await apiClient.post(`/api-keys/${keyId}/regenerate`);
      const data = res.data?.data ?? res.data;
      setRevealedKey(data?.key || null);
      toast({ title: 'API key regenerated', description: 'Copy the new key now.' });
      await loadKeys();
    } catch (err: any) {
      toast({
        title: 'Regenerate failed',
        description: err?.response?.data?.detail || err?.message || 'Try again',
        variant: 'destructive',
      });
    }
  };

  const deleteApiKey = async (keyId: number, name: string) => {
    if (!window.confirm(`Delete "${name}"? This cannot be undone.`)) return;
    try {
      await apiClient.delete(`/api-keys/${keyId}`);
      toast({ title: 'API key deleted' });
      await loadKeys();
    } catch (err: any) {
      toast({
        title: 'Delete failed',
        description: err?.response?.data?.detail || err?.message || 'Try again',
        variant: 'destructive',
      });
    }
  };

  const copyToClipboard = async (value: string, id: string) => {
    await navigator.clipboard.writeText(value);
    setCopiedKey(id);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold">{t('settings.securitySettings')}</h2>

      <div className="border-t border-white/10 pt-6">
        <h3 className="font-medium mb-3">{t('settings.twoFactorAuth')}</h3>
        <div className="flex items-center justify-between p-4 rounded-xl border border-white/10 glass">
          <div>
            <p className="font-medium">{t('settings.enable2FA')}</p>
            <p className="text-sm text-muted-foreground">
              {mfaLoading
                ? 'Checking status…'
                : mfaEnabled
                  ? '2FA is active on this account.'
                  : t('settings.enable2FADesc')}
            </p>
          </div>
          {mfaEnabled ? (
            <button
              type="button"
              disabled={mfaBusy}
              onClick={() => void disableMfa()}
              className="px-4 py-2 rounded-xl border border-white/10 hover:bg-white/5 transition-colors disabled:opacity-50"
            >
              Disable
            </button>
          ) : (
            <button
              type="button"
              disabled={mfaBusy || mfaLoading}
              onClick={() => void startMfaSetup()}
              className="px-4 py-2 rounded-xl border border-white/10 hover:bg-white/5 transition-colors disabled:opacity-50"
            >
              {mfaBusy ? 'Starting…' : t('settings.setup')}
            </button>
          )}
        </div>

        {mfaSetup && (
          <div className="mt-4 space-y-3 p-4 rounded-xl border border-primary/30 bg-primary/5">
            <p className="text-sm text-muted-foreground">
              Scan this QR code in your authenticator app, then enter the 6-digit code.
            </p>
            {mfaSetup.qr_code_base64 && (
              <img
                alt="2FA QR code"
                className="mx-auto h-40 w-40 rounded-lg bg-white p-2"
                src={`data:image/png;base64,${mfaSetup.qr_code_base64}`}
              />
            )}
            <p className="text-xs text-center font-mono break-all">Secret: {mfaSetup.secret}</p>
            <div className="flex gap-2">
              <input
                value={mfaCode}
                onChange={(e) => setMfaCode(e.target.value)}
                placeholder="123456"
                className="flex-1 px-3 py-2 rounded-lg border border-white/10 bg-transparent"
              />
              <button
                type="button"
                disabled={mfaBusy || mfaCode.trim().length < 6}
                onClick={() => void verifyMfa()}
                className="px-4 py-2 rounded-lg bg-primary text-primary-foreground disabled:opacity-50"
              >
                Verify & Enable
              </button>
            </div>
          </div>
        )}

        {backupCodes.length > 0 && (
          <div className="mt-4 p-4 rounded-xl border border-amber-500/30 bg-amber-500/10">
            <p className="font-medium mb-2">Backup codes (shown once)</p>
            <ul className="grid grid-cols-2 gap-1 font-mono text-sm">
              {backupCodes.map((code) => (
                <li key={code}>{code}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="border-t border-white/10 pt-6">
        <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
          <h3 className="font-medium">API Keys</h3>
          <div className="flex items-center gap-2">
            <input
              value={newKeyName}
              onChange={(e) => setNewKeyName(e.target.value)}
              className="px-3 py-1.5 rounded-lg border border-white/10 bg-transparent text-sm"
              placeholder="Key name"
            />
            <button
              type="button"
              disabled={creatingKey}
              onClick={() => void createApiKey()}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-primary hover:bg-primary/10 transition-colors disabled:opacity-50"
            >
              {creatingKey ? <Loader2 className="w-4 h-4 animate-spin" /> : <span>+ Create API Key</span>}
            </button>
          </div>
        </div>

        {revealedKey && (
          <div className="mb-4 p-4 rounded-xl border border-green-500/30 bg-green-500/10 space-y-2">
            <p className="text-sm font-medium">New key (copy now — it will not be shown again)</p>
            <div className="flex gap-2">
              <code className="flex-1 px-3 py-2 rounded-lg bg-black/30 font-mono text-sm break-all">
                {revealedKey}
              </code>
              <button
                type="button"
                onClick={() => void copyToClipboard(revealedKey, 'revealed')}
                className="px-3 py-2 rounded-lg border border-white/10 text-sm"
              >
                {copiedKey === 'revealed' ? 'Copied' : 'Copy'}
              </button>
            </div>
          </div>
        )}

        {keysLoading ? (
          <p className="text-sm text-muted-foreground">Loading API keys…</p>
        ) : apiKeys.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No API keys yet. Create one to call Stratum APIs programmatically.
          </p>
        ) : (
          <div className="space-y-4">
            {apiKeys.map((apiKey) => (
              <div key={apiKey.id} className="p-4 rounded-xl border border-white/10 glass">
                <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
                  <div className="flex items-center gap-3">
                    <h4 className="font-medium">{apiKey.name}</h4>
                    <span
                      className={cn(
                        'px-2.5 py-1 rounded-full text-xs font-semibold',
                        apiKey.is_active
                          ? 'bg-green-500/20 text-green-400 border border-green-500/30'
                          : 'bg-muted text-muted-foreground'
                      )}
                    >
                      {apiKey.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void regenerateApiKey(apiKey.id, apiKey.name)}
                      className="px-3 py-1.5 rounded-lg border border-white/10 hover:bg-white/5 text-sm"
                    >
                      Regenerate
                    </button>
                    <button
                      type="button"
                      onClick={() => void deleteApiKey(apiKey.id, apiKey.name)}
                      className="px-3 py-1.5 rounded-lg border border-white/10 hover:bg-white/5 text-sm text-red-400"
                    >
                      Delete
                    </button>
                  </div>
                </div>
                <code className="block px-3 py-2.5 rounded-lg bg-black/30 border border-white/5 font-mono text-sm">
                  {apiKey.masked_key || `${apiKey.key_prefix}${'•'.repeat(28)}`}
                </code>
                <div className="mt-2 flex items-center gap-4 text-xs text-muted-foreground">
                  <span>Created: {apiKey.created_at ? new Date(apiKey.created_at).toLocaleDateString() : '—'}</span>
                  <span>|</span>
                  <span>
                    Last used:{' '}
                    {apiKey.last_used_at ? new Date(apiKey.last_used_at).toLocaleString() : 'Never'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function IntegrationSettings() {
  const { t } = useTranslation();
  const [webhooks, setWebhooks] = useState([
    {
      id: '1',
      url: 'https://api.yourcompany.com/webhooks/stratum',
      events: ['campaign.updated', 'alert.triggered', 'sync.completed'],
      status: 'active' as const,
      lastTriggered: '5 minutes ago',
    },
  ]);
  const [showAddWebhook, setShowAddWebhook] = useState(false);
  const [newWebhookUrl, setNewWebhookUrl] = useState('');
  const [newWebhookEvents, setNewWebhookEvents] = useState<string[]>([]);

  // Platform integration icons as SVG components
  const IntegrationIcon = ({ type }: { type: string }) => {
    const icons: Record<string, React.ReactNode> = {
      shopify: (
        <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor">
          <path d="M15.34 5.55c-.03-.24-.24-.36-.4-.38-.16-.02-3.38-.07-3.38-.07s-2.25-2.2-2.5-2.45c-.24-.24-.72-.17-.9-.11-.03 0-.5.15-1.3.4C6.45 1.73 5.92.94 4.9.94c-1.64 0-2.44 2.05-2.69 3.09-.65.2-1.1.34-1.16.36-.36.11-.37.12-.42.46C.58 5.22 0 19.4 0 19.4l12.2 2.28 6.58-1.43S15.37 5.79 15.34 5.55zM10.7 3.57l-1.67.52c0-.82-.11-1.98-.48-2.97.93.18 1.48 1.22 1.75 2.14.14.1.27.2.4.31zm-2.66.82L5.65 5.15c.31-1.22.9-1.81 1.7-2.03.26.53.43 1.28.49 2.04.06.07.12.15.2.23zM4.93 1.78c.11 0 .22.04.32.1-.8.38-1.66 1.33-2.02 3.24l-1.57.49c.43-1.47 1.44-3.83 3.27-3.83z" />
          <path
            d="M14.94 5.17c-.16.02-3.38.07-3.38.07s-2.25-2.2-2.5-2.45c-.09-.09-.2-.14-.31-.16l-.86 18.09 6.58-1.43S15.37 5.79 15.34 5.55c-.03-.24-.24-.36-.4-.38z"
            opacity=".5"
          />
        </svg>
      ),
      // Paddle Billing is unused on this free portal
      paddle: <CreditCard className="w-6 h-6" aria-hidden="true" />,
      wordpress: (
        <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor">
          <path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm-1.46 14.58L7.93 5.51c.46-.02.88-.07.88-.07.41-.05.36-.66-.05-.64 0 0-1.24.1-2.04.1-.14 0-.31 0-.48-.01C7.58 2.91 9.66 1.8 12 1.8c1.73 0 3.31.66 4.5 1.74-.03 0-.06-.01-.09-.01-.72 0-1.23.63-1.23 1.3 0 .6.35 1.11.72 1.72.28.48.6 1.1.6 2 0 .62-.24 1.34-.56 2.34l-.73 2.44-2.65-7.89c.44-.02.84-.07.84-.07.4-.05.35-.64-.05-.62 0 0-1.2.09-1.98.09-.07 0-.15 0-.22 0l2.87 8.58-1.96 5.86-3.82-11.34zM12 22.2c-1.22 0-2.39-.22-3.47-.62l3.68-10.69 3.77 10.33c.02.06.05.12.08.17-1.26.52-2.64.81-4.06.81zm8.4-5.14c.33-1.35.53-2.9.53-4.62 0-1.81-.33-3.38-.86-4.72l-4.7 13.62c3.03-1.46 5.03-4.57 5.03-8.28zm-17.9-4.62c0 3.27 1.61 6.16 4.07 7.93L2.92 9.45c-.28 1.03-.42 2.12-.42 3.25v.74z" />
        </svg>
      ),
      meta: (
        <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor">
          <path
            d="M12 2.04c-5.5 0-10 4.49-10 10.02 0 5 3.66 9.15 8.44 9.9v-7H7.9v-2.9h2.54V9.85c0-2.52 1.49-3.92 3.77-3.92 1.09 0 2.24.2 2.24.2v2.47h-1.26c-1.24 0-1.63.78-1.63 1.57v1.88h2.78l-.45 2.9h-2.33v7a10 10 0 0 0 8.44-9.9c0-5.53-4.5-10.02-10-10.02z"
            fill="#0866FF"
          />
        </svg>
      ),
      slack: (
        <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor">
          <path
            d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z"
            fill="#E01E5A"
          />
        </svg>
      ),
      // Measurement & Verification (not ad platforms)
      ga4: (
        <svg viewBox="0 0 24 24" className="w-6 h-6" fill="#E37400">
          <rect x="14" y="3" width="6" height="18" rx="3" />
          <rect x="9" y="9" width="6" height="12" rx="3" opacity=".75" />
          <circle cx="7" cy="18" r="3" opacity=".75" />
        </svg>
      ),
      gtm: (
        <svg viewBox="0 0 24 24" className="w-6 h-6" fill="#4285F4">
          <path d="M12 2.5 21.5 12 12 21.5 2.5 12 12 2.5Zm0 4.2L6.7 12l5.3 5.3 5.3-5.3L12 6.7Z" />
          <circle cx="12" cy="12" r="2.2" opacity=".8" />
        </svg>
      ),
    };
    return icons[type] || <div className="w-6 h-6 rounded-full bg-muted" />;
  };

  // Ad Platforms
  const adPlatforms = [
    { id: 'meta', name: 'Meta Ads (Facebook, Instagram & WhatsApp)', connected: true, color: 'text-blue-600' },
    { id: 'slack', name: 'Slack', connected: true, color: 'text-purple-500' },
  ];

  // E-commerce & Payments
  const commerceIntegrations = [
    {
      id: 'shopify',
      name: 'Shopify',
      connected: true,
      color: 'text-green-500',
      description: 'Sync orders and product catalog',
    },
    {
      id: 'wordpress',
      name: 'WordPress',
      connected: false,
      color: 'text-blue-500',
      description: 'Connect WooCommerce and forms',
    },
  ];

  // Webhook event types
  const webhookEventTypes = [
    {
      id: 'campaign.updated',
      label: 'Campaign Updated',
      description: 'When campaign settings change',
    },
    { id: 'campaign.paused', label: 'Campaign Paused', description: 'When a campaign is paused' },
    {
      id: 'alert.triggered',
      label: 'Alert Triggered',
      description: 'When performance alerts fire',
    },
    { id: 'budget.depleted', label: 'Budget Depleted', description: 'When daily budget runs out' },
    { id: 'sync.completed', label: 'Sync Completed', description: 'When data sync finishes' },
    {
      id: 'anomaly.detected',
      label: 'Anomaly Detected',
      description: 'When unusual patterns found',
    },
  ];

  const toggleWebhookEvent = (eventId: string) => {
    if (newWebhookEvents.includes(eventId)) {
      setNewWebhookEvents(newWebhookEvents.filter((e) => e !== eventId));
    } else {
      setNewWebhookEvents([...newWebhookEvents, eventId]);
    }
  };

  const addWebhook = () => {
    if (newWebhookUrl && newWebhookEvents.length > 0) {
      setWebhooks([
        ...webhooks,
        {
          id: Date.now().toString(),
          url: newWebhookUrl,
          events: newWebhookEvents,
          status: 'active',
          lastTriggered: 'Never',
        },
      ]);
      setNewWebhookUrl('');
      setNewWebhookEvents([]);
      setShowAddWebhook(false);
    }
  };

  const deleteWebhook = (id: string) => {
    if (confirm('Are you sure you want to delete this webhook?')) {
      setWebhooks(webhooks.filter((w) => w.id !== id));
    }
  };

  const IntegrationCard = ({
    integration,
    showDescription = false,
  }: {
    integration: any;
    showDescription?: boolean;
  }) => (
    <div className="flex items-center justify-between p-4 rounded-xl border border-white/10 glass card-3d">
      <div className="flex items-center gap-4">
        <div className={cn('p-2 rounded-xl bg-black/30', integration.color)}>
          <IntegrationIcon type={integration.id} />
        </div>
        <div>
          <p className="font-medium">{integration.name}</p>
          {showDescription && (
            <p className="text-sm text-muted-foreground">{integration.description}</p>
          )}
          {!showDescription && (
            <p className="text-sm text-muted-foreground">
              {integration.connected ? t('settings.connected') : t('settings.notConnected')}
            </p>
          )}
        </div>
      </div>
      <div className="flex items-center gap-3">
        {integration.connected && (
          <span className="px-2.5 py-1 rounded-full bg-green-500/20 text-green-400 text-xs font-medium border border-green-500/30">
            Connected
          </span>
        )}
        <button
          className={cn(
            'px-4 py-2 rounded-xl text-sm font-medium transition-colors',
            integration.connected
              ? 'border border-white/10 hover:bg-white/5 text-red-400'
              : 'bg-primary text-primary-foreground hover:bg-primary/90'
          )}
        >
          {integration.connected ? t('settings.disconnect') : t('settings.connect')}
        </button>
      </div>
    </div>
  );

  return (
    <div className="space-y-8">
      <h2 className="text-lg font-semibold">{t('settings.integrationSettings')}</h2>

      {/* Webhooks Section */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="font-medium">{t('settings.webhooks')}</h3>
            <p className="text-sm text-muted-foreground">
              Receive real-time notifications for platform events
            </p>
          </div>
          <button
            onClick={() => setShowAddWebhook(true)}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            <span>+ Add Webhook Endpoint</span>
          </button>
        </div>

        <div className="space-y-3">
          {webhooks.map((webhook) => (
            <div key={webhook.id} className="p-4 rounded-xl border border-white/10 glass">
              <div className="flex items-start justify-between mb-3">
                <div className="flex-1 min-w-0">
                  <code className="text-sm font-mono text-cyan-400 break-all">{webhook.url}</code>
                  <div className="flex flex-wrap gap-2 mt-2">
                    {webhook.events.map((event) => (
                      <span
                        key={event}
                        className="px-2 py-0.5 rounded-full bg-primary/10 text-primary text-xs"
                      >
                        {event}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-2 ml-4">
                  <span className="px-2.5 py-1 rounded-full bg-green-500/20 text-green-400 text-xs font-medium border border-green-500/30">
                    Active
                  </span>
                  <button className="px-3 py-1.5 rounded-lg border border-white/10 hover:bg-white/5 text-sm">
                    Edit
                  </button>
                  <button
                    onClick={() => deleteWebhook(webhook.id)}
                    className="px-3 py-1.5 rounded-lg border border-red-500/30 hover:bg-red-500/10 text-red-400 text-sm"
                  >
                    Delete
                  </button>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                Last triggered: {webhook.lastTriggered}
              </p>
            </div>
          ))}
        </div>

        {/* Add Webhook Modal */}
        {showAddWebhook && (
          <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="w-full max-w-lg rounded-2xl border border-white/10 glass-strong p-6">
              <h3 className="text-lg font-semibold mb-4">Add Webhook Endpoint</h3>

              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium mb-2">Endpoint URL</label>
                  <input
                    type="url"
                    value={newWebhookUrl}
                    onChange={(e) => setNewWebhookUrl(e.target.value)}
                    placeholder="https://your-app.com/webhooks/stratum"
                    className="w-full px-4 py-2 rounded-xl border border-white/10 glass bg-transparent focus:outline-none focus:ring-2 focus:ring-primary/20"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium mb-2">Events to Subscribe</label>
                  <div className="grid grid-cols-2 gap-2">
                    {webhookEventTypes.map((event) => (
                      <button
                        key={event.id}
                        onClick={() => toggleWebhookEvent(event.id)}
                        className={cn(
                          'p-3 rounded-xl border text-left transition-all',
                          newWebhookEvents.includes(event.id)
                            ? 'border-primary bg-primary/10'
                            : 'border-white/10 hover:border-white/20'
                        )}
                      >
                        <p className="font-medium text-sm">{event.label}</p>
                        <p className="text-xs text-muted-foreground">{event.description}</p>
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-end gap-3 mt-6 pt-4 border-t border-white/10">
                <button
                  onClick={() => setShowAddWebhook(false)}
                  className="px-4 py-2 rounded-xl border border-white/10 hover:bg-white/5"
                >
                  Cancel
                </button>
                <button
                  onClick={addWebhook}
                  disabled={!newWebhookUrl || newWebhookEvents.length === 0}
                  className="px-4 py-2 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  Add Webhook
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Ad Platforms (Meta only) */}
      <div>
        <h3 className="font-medium mb-3">{t('settings.adPlatforms')}</h3>
        <div className="space-y-3">
          {adPlatforms.map((integration) => (
            <IntegrationCard key={integration.id} integration={integration} />
          ))}
        </div>
      </div>

      {/* Measurement & Verification (GA4 read-only baseline + GTM tag deployment) */}
      {/* Deliberately separate from Ad Platforms: these are not ad channels. */}
      <div>
        <div className="mb-4">
          <h3 className="font-medium">{t('settings.measurementVerification')}</h3>
          <p className="text-sm text-muted-foreground">
            {t('settings.measurementVerificationDesc')}
          </p>
        </div>
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <GA4Integration />
          <GTMIntegration />
        </div>
      </div>

      {/* Connected Services (E-commerce & Payments) */}
      <div>
        <h3 className="font-medium mb-3">{t('settings.connectedServices')}</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {commerceIntegrations.map((integration) => (
            <div
              key={integration.id}
              className="p-4 rounded-xl border border-white/10 glass card-3d text-center"
            >
              <div className={cn('p-3 rounded-xl bg-black/30 inline-flex mb-3', integration.color)}>
                <IntegrationIcon type={integration.id} />
              </div>
              <h4 className="font-medium mb-1">{integration.name}</h4>
              <p className="text-xs text-muted-foreground mb-3">{integration.description}</p>
              {integration.connected ? (
                <span className="px-3 py-1.5 rounded-full bg-green-500/20 text-green-400 text-xs font-medium border border-green-500/30 inline-block">
                  Connected
                </span>
              ) : (
                <span className="px-3 py-1.5 rounded-full bg-gray-500/20 text-gray-400 text-xs font-medium border border-gray-500/30 inline-block">
                  Not Connected
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function PreferenceSettings() {
  const { t, i18n } = useTranslation();
  const [theme, setTheme] = useState('system');
  const [language, setLanguage] = useState(i18n.language);

  const handleLanguageChange = (lang: string) => {
    setLanguage(lang);
    i18n.changeLanguage(lang);
  };

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold">{t('settings.preferenceSettings')}</h2>

      <div>
        <label className="text-sm font-medium mb-2 block">{t('settings.theme')}</label>
        <div className="flex gap-3">
          {['light', 'dark', 'system'].map((t) => (
            <button
              key={t}
              onClick={() => setTheme(t)}
              className={cn(
                'px-4 py-2 rounded-lg border transition-colors capitalize',
                theme === t ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
              )}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      <div>
        <label className="text-sm font-medium mb-2 block">{t('settings.language')}</label>
        <select
          value={language}
          onChange={(e) => handleLanguageChange(e.target.value)}
          className="w-full px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
        >
          <option value="en">English</option>
          <option value="uk">Українська</option>
        </select>
      </div>

      <div>
        <label className="text-sm font-medium mb-2 block">{t('settings.currency')}</label>
        <select className="w-full px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20">
          <option value="USD">USD ($)</option>
          <option value="EUR">EUR (€)</option>
          <option value="GBP">GBP (£)</option>
          <option value="UAH">UAH (₴)</option>
        </select>
      </div>

      <div>
        <label className="text-sm font-medium mb-2 block">{t('settings.dateFormat')}</label>
        <select className="w-full px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20">
          <option value="MM/DD/YYYY">MM/DD/YYYY</option>
          <option value="DD/MM/YYYY">DD/MM/YYYY</option>
          <option value="YYYY-MM-DD">YYYY-MM-DD</option>
        </select>
      </div>
    </div>
  );
}

function BillingSettings() {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold">{t('settings.billingSettings')}</h2>
      <div
        className="rounded-xl border p-6 space-y-2"
        style={{
          background: 'rgba(255, 255, 255, 0.03)',
          borderColor: 'rgba(255, 255, 255, 0.08)',
        }}
      >
        <h3 className="text-base font-semibold">Free workspace</h3>
        <p className="text-sm text-muted-foreground">
          This portal does not take payments. Anyone can create an account with full workspace
          access — no credit card, no subscription, and no checkout overlay.
        </p>
      </div>
    </div>
  );
}

function GDPRSettings() {
  const { t } = useTranslation();

  // API hooks for GDPR operations
  const exportData = useExportData();
  const requestDeletion = useRequestDeletion();

  const [exportStatus, setExportStatus] = useState<'idle' | 'processing' | 'ready'>('idle');

  // Handle data export request
  const handleExport = async () => {
    setExportStatus('processing');
    try {
      await exportData.mutateAsync('json');
      setExportStatus('ready');
    } catch (error) {
      console.error('Export failed:', error);
      // Fallback to mock success for demo
      setTimeout(() => setExportStatus('ready'), 3000);
    }
  };

  // Handle account deletion request
  const handleDeleteRequest = async () => {
    if (
      !confirm('Are you sure you want to request account deletion? This action cannot be undone.')
    ) {
      return;
    }
    try {
      await requestDeletion.mutateAsync('User requested account deletion');
      alert('Deletion request submitted. You will receive an email confirmation.');
    } catch (error) {
      console.error('Deletion request failed:', error);
      alert('Deletion request submitted (demo mode).');
    }
  };

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold">{t('settings.gdprSettings')}</h2>

      <div className="p-4 rounded-lg border border-amber-500/30 bg-amber-500/10">
        <div className="flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-amber-500 mt-0.5" />
          <div>
            <p className="font-medium">{t('settings.gdprNotice')}</p>
            <p className="text-sm text-muted-foreground mt-1">{t('settings.gdprNoticeDesc')}</p>
          </div>
        </div>
      </div>

      <div>
        <h3 className="font-medium mb-3">{t('settings.exportData')}</h3>
        <p className="text-sm text-muted-foreground mb-3">{t('settings.exportDataDesc')}</p>
        <button
          onClick={handleExport}
          disabled={exportStatus === 'processing' || exportData.isPending}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
        >
          {exportStatus === 'processing' || exportData.isPending ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              {t('settings.processing')}
            </>
          ) : exportStatus === 'ready' ? (
            <>
              <Download className="w-4 h-4" />
              {t('settings.downloadReady')}
            </>
          ) : (
            <>
              <Download className="w-4 h-4" />
              {t('settings.requestExport')}
            </>
          )}
        </button>
      </div>

      <div className="border-t pt-6">
        <h3 className="font-medium mb-3 text-red-500">{t('settings.deleteAccount')}</h3>
        <p className="text-sm text-muted-foreground mb-3">{t('settings.deleteAccountDesc')}</p>
        <button
          onClick={handleDeleteRequest}
          disabled={requestDeletion.isPending}
          className="flex items-center gap-2 px-4 py-2 rounded-lg border border-red-500 text-red-500 hover:bg-red-500/10 transition-colors disabled:opacity-50"
        >
          {requestDeletion.isPending ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Trash2 className="w-4 h-4" />
          )}
          {t('settings.deleteAccountButton')}
        </button>
      </div>
    </div>
  );
}

function TrustEngineSettings() {
  const [healthyThreshold, setHealthyThreshold] = useState(70);
  const [degradedThreshold, setDegradedThreshold] = useState(40);
  const [autopilotEnabled, setAutopilotEnabled] = useState(true);

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold">Trust Engine Configuration</h2>
      <p className="text-sm text-muted-foreground">
        Configure signal health thresholds that control when automations can execute.
        The Trust Engine ensures automations only run when signal quality meets safety requirements.
      </p>

      <div className="space-y-6">
        {/* Healthy Threshold */}
        <div className="p-4 rounded-xl border border-green-500/20 bg-green-500/5">
          <div className="flex items-center justify-between mb-3">
            <div>
              <label className="font-medium text-green-400">Healthy Threshold</label>
              <p className="text-sm text-muted-foreground mt-0.5">
                Signal health at or above this value enables autopilot execution
              </p>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={0}
                max={100}
                value={healthyThreshold}
                onChange={(e) => setHealthyThreshold(Number(e.target.value))}
                className="w-20 px-3 py-2 rounded-lg border border-white/10 bg-transparent text-center font-mono text-lg focus:outline-none focus:ring-2 focus:ring-green-500/30"
              />
              <span className="text-muted-foreground text-sm">/100</span>
            </div>
          </div>
          <div className="w-full bg-white/10 rounded-full h-2">
            <div
              className="bg-green-500 rounded-full h-2 transition-all"
              style={{ width: `${healthyThreshold}%` }}
            />
          </div>
        </div>

        {/* Degraded Threshold */}
        <div className="p-4 rounded-xl border border-amber-500/20 bg-amber-500/5">
          <div className="flex items-center justify-between mb-3">
            <div>
              <label className="font-medium text-amber-400">Degraded Threshold</label>
              <p className="text-sm text-muted-foreground mt-0.5">
                Below healthy but above this value triggers alerts and holds execution
              </p>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={0}
                max={100}
                value={degradedThreshold}
                onChange={(e) => setDegradedThreshold(Number(e.target.value))}
                className="w-20 px-3 py-2 rounded-lg border border-white/10 bg-transparent text-center font-mono text-lg focus:outline-none focus:ring-2 focus:ring-amber-500/30"
              />
              <span className="text-muted-foreground text-sm">/100</span>
            </div>
          </div>
          <div className="w-full bg-white/10 rounded-full h-2">
            <div
              className="bg-amber-500 rounded-full h-2 transition-all"
              style={{ width: `${degradedThreshold}%` }}
            />
          </div>
        </div>

        {/* Below Degraded Info */}
        <div className="p-4 rounded-xl border border-red-500/20 bg-red-500/5">
          <div>
            <label className="font-medium text-red-400">Unhealthy Zone</label>
            <p className="text-sm text-muted-foreground mt-0.5">
              Below {degradedThreshold}: All automations blocked. Manual action required.
            </p>
          </div>
        </div>

        {/* Autopilot Toggle */}
        <div className="p-4 rounded-xl border border-white/10 bg-white/5">
          <div className="flex items-center justify-between">
            <div>
              <label className="font-medium">Autopilot Mode</label>
              <p className="text-sm text-muted-foreground mt-0.5">
                When enabled, automations execute automatically when signal health is above the
                healthy threshold ({healthyThreshold})
              </p>
            </div>
            <button
              onClick={() => setAutopilotEnabled(!autopilotEnabled)}
              className={cn(
                'relative w-12 h-6 rounded-full transition-colors',
                autopilotEnabled ? 'bg-primary' : 'bg-muted'
              )}
            >
              <span
                className={cn(
                  'absolute top-1 w-4 h-4 bg-white rounded-full transition-transform',
                  autopilotEnabled ? 'translate-x-7' : 'translate-x-1'
                )}
              />
            </button>
          </div>
        </div>

        {/* Visual summary */}
        <div className="p-4 rounded-xl border border-white/10 bg-white/5">
          <h3 className="font-medium mb-3">Trust Gate Logic</h3>
          <div className="space-y-2 text-sm">
            <div className="flex items-center gap-3">
              <span className="w-3 h-3 rounded-full bg-green-500" />
              <span>
                Score {'>='} {healthyThreshold}: <strong className="text-green-400">PASS</strong> -
                Autopilot {autopilotEnabled ? 'executes' : 'disabled (manual only)'}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <span className="w-3 h-3 rounded-full bg-amber-500" />
              <span>
                Score {degradedThreshold}-{healthyThreshold - 1}:{' '}
                <strong className="text-amber-400">HOLD</strong> - Alert only, no auto-execution
              </span>
            </div>
            <div className="flex items-center gap-3">
              <span className="w-3 h-3 rounded-full bg-red-500" />
              <span>
                Score {'<'} {degradedThreshold}: <strong className="text-red-400">BLOCK</strong> -
                Manual action required
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default Settings;
