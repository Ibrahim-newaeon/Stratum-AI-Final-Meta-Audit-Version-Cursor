/**
 * CDP Consent Manager — real tenant consent records (read-only).
 */

import { useMemo, useState } from 'react';
import {
  CheckCircle,
  Filter,
  RefreshCw,
  Search,
  Shield,
  Users,
  XCircle,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useConsentProfiles, useConsentStats } from '@/api/cdp';

const CONSENT_TYPES = [
  { value: 'analytics', label: 'Analytics', description: 'Website analytics and tracking' },
  { value: 'ads', label: 'Advertising', description: 'Personalized advertising' },
  { value: 'email', label: 'Email Marketing', description: 'Marketing emails' },
  { value: 'sms', label: 'SMS Marketing', description: 'Marketing text messages' },
];

export function ConsentManager() {
  const [selectedType, setSelectedType] = useState<string | undefined>(undefined);
  const [grantedFilter, setGrantedFilter] = useState<boolean | undefined>(undefined);
  const [searchQuery, setSearchQuery] = useState('');

  const {
    data: stats,
    isLoading: statsLoading,
    isError: statsError,
    refetch: refetchStats,
  } = useConsentStats();
  const {
    data: profilesData,
    isLoading: profilesLoading,
    isError: profilesError,
    refetch: refetchProfiles,
  } = useConsentProfiles({
    consent_type: selectedType,
    granted: grantedFilter,
    limit: 50,
  });

  const profiles = useMemo(() => {
    const rows = profilesData?.profiles || [];
    const q = searchQuery.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (p) =>
        (p.email || '').toLowerCase().includes(q) ||
        p.profile_id.toLowerCase().includes(q) ||
        p.consent_type.toLowerCase().includes(q)
    );
  }, [profilesData, searchQuery]);

  const handleRefresh = () => {
    void refetchStats();
    void refetchProfiles();
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold flex items-center gap-2">
            <Shield className="w-6 h-6 text-primary" />
            Consent Management
          </h2>
          <p className="text-muted-foreground">
            Live consent records for this tenant. Empty until CDP profiles record consent.
          </p>
        </div>
        <button
          onClick={handleRefresh}
          className="px-3 py-2 rounded-lg border hover:bg-muted transition-colors"
          aria-label="Refresh consent data"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {(statsError || profilesError) && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          Could not load consent data. Check that you are signed in and the CDP API is reachable.
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {statsLoading
          ? Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="p-4 rounded-xl border bg-card animate-pulse">
                <div className="h-4 w-24 bg-muted rounded mb-2" />
                <div className="h-8 w-16 bg-muted rounded" />
              </div>
            ))
          : (stats || []).length === 0
            ? (
              <div className="col-span-full p-6 rounded-xl border bg-card text-sm text-muted-foreground">
                No consent records yet. Stats appear after profiles capture consent preferences.
              </div>
            )
            : (stats || []).map((stat) => {
                const typeInfo = CONSENT_TYPES.find((t) => t.value === stat.consent_type);
                return (
                  <div
                    key={stat.consent_type}
                    className={cn(
                      'p-4 rounded-xl border bg-card cursor-pointer hover:border-primary/50 transition-colors',
                      selectedType === stat.consent_type && 'border-primary'
                    )}
                    onClick={() =>
                      setSelectedType(
                        selectedType === stat.consent_type ? undefined : stat.consent_type
                      )
                    }
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-muted-foreground">
                        {typeInfo?.label || stat.consent_type}
                      </span>
                      <span
                        className={cn(
                          'text-xs px-2 py-0.5 rounded-full',
                          stat.grant_rate >= 70
                            ? 'bg-green-500/10 text-green-600'
                            : stat.grant_rate >= 50
                              ? 'bg-yellow-500/10 text-yellow-600'
                              : 'bg-red-500/10 text-red-600'
                        )}
                      >
                        {stat.grant_rate.toFixed(1)}%
                      </span>
                    </div>
                    <div className="text-2xl font-bold">{stat.granted.toLocaleString()}</div>
                    <div className="text-xs text-muted-foreground">
                      of {stat.total_profiles.toLocaleString()} profiles
                    </div>
                    <div className="mt-2 flex items-center gap-2 text-xs">
                      <span className="flex items-center gap-1 text-green-600">
                        <CheckCircle className="w-3 h-3" />
                        {stat.granted}
                      </span>
                      <span className="flex items-center gap-1 text-red-600">
                        <XCircle className="w-3 h-3" />
                        {stat.revoked}
                      </span>
                    </div>
                  </div>
                );
              })}
      </div>

      <div className="flex items-center gap-4 p-4 rounded-xl border bg-card">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search by email or profile ID..."
            className="w-full pl-10 pr-4 py-2 rounded-lg border bg-background"
          />
        </div>
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-muted-foreground" />
          <select
            value={selectedType || ''}
            onChange={(e) => setSelectedType(e.target.value || undefined)}
            className="px-3 py-2 rounded-lg border bg-background"
          >
            <option value="">All Types</option>
            {CONSENT_TYPES.map((type) => (
              <option key={type.value} value={type.value}>
                {type.label}
              </option>
            ))}
          </select>
          <select
            value={grantedFilter === undefined ? '' : grantedFilter.toString()}
            onChange={(e) =>
              setGrantedFilter(e.target.value === '' ? undefined : e.target.value === 'true')
            }
            className="px-3 py-2 rounded-lg border bg-background"
          >
            <option value="">All Status</option>
            <option value="true">Granted</option>
            <option value="false">Revoked</option>
          </select>
        </div>
      </div>

      <div className="rounded-xl border bg-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-muted/50">
              <tr>
                <th className="px-4 py-3 text-left text-sm font-medium">Profile</th>
                <th className="px-4 py-3 text-left text-sm font-medium">Consent Type</th>
                <th className="px-4 py-3 text-left text-sm font-medium">Status</th>
                <th className="px-4 py-3 text-left text-sm font-medium">Date</th>
                <th className="px-4 py-3 text-left text-sm font-medium">Source</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {profilesLoading ? (
                Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i}>
                    <td className="px-4 py-3" colSpan={5}>
                      <div className="h-4 w-full bg-muted rounded animate-pulse" />
                    </td>
                  </tr>
                ))
              ) : profiles.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-muted-foreground">
                    <Users className="w-8 h-8 mx-auto mb-2 opacity-50" />
                    No consent records found for this filter.
                  </td>
                </tr>
              ) : (
                profiles.map((profile) => {
                  const typeInfo = CONSENT_TYPES.find((t) => t.value === profile.consent_type);
                  return (
                    <tr
                      key={`${profile.profile_id}-${profile.consent_type}`}
                      className="hover:bg-muted/50"
                    >
                      <td className="px-4 py-3">
                        <div>
                          <div className="font-medium">{profile.email || 'Anonymous'}</div>
                          <div className="text-xs text-muted-foreground font-mono">
                            {profile.profile_id}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className="px-2 py-1 rounded-full text-xs bg-muted">
                          {typeInfo?.label || profile.consent_type}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        {profile.granted ? (
                          <span className="flex items-center gap-1 text-green-600">
                            <CheckCircle className="w-4 h-4" />
                            Granted
                          </span>
                        ) : (
                          <span className="flex items-center gap-1 text-red-600">
                            <XCircle className="w-4 h-4" />
                            Revoked
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-sm text-muted-foreground">
                        {profile.granted_at
                          ? new Date(profile.granted_at).toLocaleDateString()
                          : profile.revoked_at
                            ? new Date(profile.revoked_at).toLocaleDateString()
                            : '—'}
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-xs text-muted-foreground capitalize">
                          {profile.source?.replace(/_/g, ' ') || '—'}
                        </span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        <div className="px-4 py-3 border-t text-sm text-muted-foreground">
          Showing {profiles.length} of {profilesData?.total || 0} records
        </div>
      </div>
    </div>
  );
}
