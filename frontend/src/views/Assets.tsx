import { useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useToast } from '@/components/ui/use-toast';
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Copy,
  Download,
  Eye,
  FileText,
  Grid3X3,
  Image as ImageIcon,
  List,
  Loader2,
  Pencil,
  Search,
  Trash2,
  Upload,
  Video,
  X,
} from 'lucide-react';
import { cn, formatCompactNumber, formatPercent } from '@/lib/utils';
import {
  useAssets,
  useBulkArchiveAssets,
  useDeleteAsset,
  useUpdateAsset,
  useUploadAsset,
} from '@/api/hooks';
import { useTenantStore } from '@/stores/tenantStore';

type AssetType = 'image' | 'video' | 'copy' | 'carousel' | 'text' | 'html5';
type AssetStatus = 'active' | 'paused' | 'fatigued' | 'draft' | 'archived';

interface Asset {
  id: number;
  name: string;
  type: AssetType;
  status: AssetStatus;
  thumbnail: string;
  url: string;
  impressions: number;
  ctr: number;
  fatigueScore: number;
  campaigns: string[];
  createdAt: string;
  dimensions?: string;
  duration?: string;
}

type ViewMode = 'grid' | 'list';

function normalizeType(raw: unknown): AssetType {
  const value = String(raw || 'image').toLowerCase();
  if (value === 'video') return 'video';
  if (value === 'copy' || value === 'text') return 'copy';
  if (value === 'carousel') return 'carousel';
  if (value === 'html5') return 'html5';
  return 'image';
}

function normalizeStatus(raw: unknown, fatigueScore = 0): AssetStatus {
  const value = String(raw || '').toLowerCase();
  if (['active', 'paused', 'fatigued', 'draft', 'archived'].includes(value)) {
    return value as AssetStatus;
  }
  if (fatigueScore >= 70) return 'fatigued';
  return 'active';
}

export function Assets() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const [searchQuery, setSearchQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [viewMode, setViewMode] = useState<ViewMode>('grid');
  const [selectedAssets, setSelectedAssets] = useState<number[]>([]);
  const [previewAsset, setPreviewAsset] = useState<Asset | null>(null);
  const [editingName, setEditingName] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useTenantStore((state) => state.tenantId);

  const { data: assetsData, isLoading, isError, refetch } = useAssets();
  const deleteAsset = useDeleteAsset();
  const bulkArchive = useBulkArchiveAssets();
  const uploadAsset = useUploadAsset();
  const updateAsset = useUpdateAsset();

  const assets = useMemo((): Asset[] => {
    const items = assetsData?.items ?? [];
    return items.map((a: any) => {
      const url = a.file_url || a.url || a.thumbnail_url || '';
      return {
        id: Number(a.id) || 0,
        name: a.name || a.filename || 'Untitled asset',
        type: normalizeType(a.asset_type || a.type),
        status: normalizeStatus(a.status, Number(a.fatigue_score ?? a.fatigueScore ?? 0)),
        thumbnail: a.thumbnail_url || url || '',
        url,
        impressions: Number(a.impressions || 0),
        ctr: Number(a.ctr || 0),
        fatigueScore: Number(a.fatigue_score ?? a.fatigueScore ?? 0),
        campaigns: Array.isArray(a.campaigns) ? a.campaigns : [],
        createdAt: a.created_at || a.createdAt || new Date().toISOString(),
        dimensions:
          a.dimensions ||
          (a.width && a.height ? `${a.width}x${a.height}` : undefined),
        duration: a.duration ? String(a.duration) : undefined,
      };
    });
  }, [assetsData]);

  const filteredAssets = assets.filter((asset) => {
    if (searchQuery && !asset.name.toLowerCase().includes(searchQuery.toLowerCase())) {
      return false;
    }
    if (typeFilter !== 'all' && asset.type !== typeFilter) {
      return false;
    }
    if (statusFilter !== 'all' && asset.status !== statusFilter) {
      return false;
    }
    return true;
  });

  const toggleSelectAsset = (id: number) => {
    setSelectedAssets((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  };

  const openPreview = (asset: Asset) => {
    setPreviewAsset(asset);
    setEditingName(asset.name);
    setIsEditing(false);
  };

  const handleUpload = async (file: File) => {
    try {
      await uploadAsset.mutateAsync({ file });
      toast({
        title: 'Asset uploaded',
        description: `"${file.name}" is now in your library.`,
      });
      await refetch();
    } catch (err) {
      toast({
        title: 'Upload failed',
        description: err instanceof Error ? err.message : 'Could not upload asset',
        variant: 'destructive',
      });
    }
  };

  const handleSaveName = async () => {
    if (!previewAsset || !editingName.trim()) return;
    try {
      await updateAsset.mutateAsync({
        id: String(previewAsset.id),
        data: { name: editingName.trim() },
      });
      toast({ title: 'Asset updated', description: 'Name saved successfully.' });
      setIsEditing(false);
      setPreviewAsset((prev) => (prev ? { ...prev, name: editingName.trim() } : prev));
      await refetch();
    } catch (err) {
      toast({
        title: 'Update failed',
        description: err instanceof Error ? err.message : 'Could not update asset',
        variant: 'destructive',
      });
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteAsset.mutateAsync(String(id));
      toast({ title: 'Asset deleted' });
      if (previewAsset?.id === id) setPreviewAsset(null);
      setSelectedAssets((prev) => prev.filter((item) => item !== id));
      await refetch();
    } catch (err) {
      toast({
        title: 'Delete failed',
        description: err instanceof Error ? err.message : 'Could not delete asset',
        variant: 'destructive',
      });
    }
  };

  const handleBulkDelete = async () => {
    try {
      await bulkArchive.mutateAsync(selectedAssets.map(String));
      toast({ title: 'Assets archived', description: `${selectedAssets.length} item(s) archived.` });
      setSelectedAssets([]);
      await refetch();
    } catch (err) {
      toast({
        title: 'Archive failed',
        description: err instanceof Error ? err.message : 'Could not archive assets',
        variant: 'destructive',
      });
    }
  };

  const getTypeIcon = (type: AssetType) => {
    if (type === 'video') return <Video className="w-4 h-4" />;
    if (type === 'copy' || type === 'text') return <FileText className="w-4 h-4" />;
    return <ImageIcon className="w-4 h-4" />;
  };

  const getStatusBadge = (status: AssetStatus) => {
    const config: Record<
      AssetStatus,
      { color: string; icon: typeof CheckCircle2; label: string }
    > = {
      active: { color: 'bg-green-500/10 text-green-500', icon: CheckCircle2, label: 'Active' },
      paused: { color: 'bg-amber-500/10 text-amber-500', icon: Clock, label: 'Paused' },
      fatigued: { color: 'bg-red-500/10 text-red-500', icon: AlertTriangle, label: 'Fatigued' },
      draft: { color: 'bg-gray-500/10 text-gray-500', icon: FileText, label: 'Draft' },
      archived: { color: 'bg-gray-500/10 text-gray-500', icon: FileText, label: 'Archived' },
    };
    const { color, icon: Icon, label } = config[status];
    return (
      <span className={cn('px-2 py-1 rounded-full text-xs font-medium inline-flex items-center gap-1', color)}>
        <Icon className="w-3 h-3" />
        {label}
      </span>
    );
  };

  const getFatigueColor = (score: number) => {
    if (score >= 70) return 'text-red-500 bg-red-500';
    if (score >= 40) return 'text-amber-500 bg-amber-500';
    return 'text-green-500 bg-green-500';
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">{t('assets.title')}</h1>
          <p className="text-muted-foreground">{t('assets.subtitle')}</p>
        </div>

        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*,video/*,.txt,.csv,.json"
            className="hidden"
            data-testid="assets-file-input"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              await handleUpload(file);
              e.target.value = '';
            }}
          />
          <button
            type="button"
            data-testid="assets-upload-button"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadAsset.isPending}
            className="relative z-10 flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
          >
            {uploadAsset.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Upload className="w-4 h-4" />
            )}
            <span>{t('assets.upload')}</span>
          </button>
        </div>
      </div>

      <div className="flex flex-col md:flex-row gap-4">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <input
            type="text"
            placeholder={t('assets.searchPlaceholder')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </div>

        <div className="flex gap-3">
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
          >
            <option value="all">{t('assets.allTypes')}</option>
            <option value="image">{t('assets.images')}</option>
            <option value="video">{t('assets.videos')}</option>
            <option value="copy">{t('assets.copy')}</option>
          </select>

          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
          >
            <option value="all">{t('assets.allStatuses')}</option>
            <option value="active">{t('assets.active')}</option>
            <option value="paused">{t('assets.paused')}</option>
            <option value="fatigued">{t('assets.fatigued')}</option>
            <option value="draft">{t('assets.draft')}</option>
          </select>

          <div className="flex rounded-lg border overflow-hidden">
            <button
              type="button"
              onClick={() => setViewMode('grid')}
              className={cn(
                'p-2 transition-colors',
                viewMode === 'grid' ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
              )}
            >
              <Grid3X3 className="w-4 h-4" />
            </button>
            <button
              type="button"
              onClick={() => setViewMode('list')}
              className={cn(
                'p-2 transition-colors',
                viewMode === 'list' ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
              )}
            >
              <List className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {selectedAssets.length > 0 && (
        <div className="flex items-center gap-4 p-3 rounded-lg bg-primary/10 border border-primary/20">
          <span className="text-sm font-medium">
            {selectedAssets.length} {t('assets.selected')}
          </span>
          <button
            type="button"
            onClick={handleBulkDelete}
            disabled={bulkArchive.isPending}
            className="flex items-center gap-1 px-3 py-1.5 rounded-md bg-red-500/10 text-red-500 hover:bg-red-500/20 transition-colors text-sm disabled:opacity-50"
          >
            {bulkArchive.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Trash2 className="w-4 h-4" />
            )}
            {t('assets.delete')}
          </button>
        </div>
      )}

      {isLoading ? (
        <div className="flex items-center justify-center py-16 text-muted-foreground gap-2">
          <Loader2 className="w-5 h-5 animate-spin" />
          Loading assets...
        </div>
      ) : isError ? (
        <div className="text-center py-12 space-y-3">
          <p className="text-muted-foreground">Could not load assets from the API.</p>
          <button
            type="button"
            onClick={() => void refetch()}
            className="px-4 py-2 rounded-lg border hover:bg-muted text-sm"
          >
            Retry
          </button>
        </div>
      ) : viewMode === 'grid' ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {filteredAssets.map((asset) => (
            <div
              key={asset.id}
              className={cn(
                'rounded-xl border bg-card overflow-hidden hover:shadow-md transition-shadow',
                selectedAssets.includes(asset.id) && 'ring-2 ring-primary'
              )}
            >
              <div
                className="relative aspect-video bg-muted cursor-pointer"
                onClick={() => openPreview(asset)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') openPreview(asset);
                }}
                role="button"
                tabIndex={0}
              >
                {asset.thumbnail ? (
                  <img
                    src={asset.thumbnail}
                    alt={asset.name}
                    className="w-full h-full object-cover"
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center text-muted-foreground">
                    {getTypeIcon(asset.type)}
                  </div>
                )}
                <div className="absolute top-2 left-2" onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    checked={selectedAssets.includes(asset.id)}
                    onChange={() => toggleSelectAsset(asset.id)}
                    className="rounded border-white/50 bg-black/30"
                  />
                </div>
                <div className="absolute top-2 right-2">{getStatusBadge(asset.status)}</div>
              </div>

              <div className="p-4">
                <h4 className="font-medium text-sm truncate mb-2">{asset.name}</h4>
                <div className="flex items-center justify-between text-sm mb-3">
                  <div className="flex items-center gap-1 text-muted-foreground">
                    <Eye className="w-3 h-3" />
                    <span>{formatCompactNumber(asset.impressions)}</span>
                  </div>
                  <div className="text-muted-foreground">
                    CTR: <span className="font-medium">{formatPercent(asset.ctr)}</span>
                  </div>
                </div>

                {asset.status !== 'draft' && (
                  <div className="space-y-1 mb-3">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">{t('assets.fatigueScore')}</span>
                      <span className={cn('font-medium', getFatigueColor(asset.fatigueScore).split(' ')[0])}>
                        {asset.fatigueScore}%
                      </span>
                    </div>
                    <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                      <div
                        className={cn('h-full rounded-full', getFatigueColor(asset.fatigueScore).split(' ')[1])}
                        style={{ width: `${Math.min(100, asset.fatigueScore)}%` }}
                      />
                    </div>
                  </div>
                )}

                <div className="flex items-center justify-between pt-3 border-t">
                  <div className="flex gap-1">
                    <button
                      type="button"
                      onClick={() => openPreview(asset)}
                      className="p-1.5 rounded hover:bg-muted transition-colors"
                      title="View"
                    >
                      <Eye className="w-4 h-4" />
                    </button>
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(asset.url || asset.thumbnail);
                          toast({ title: 'Copied', description: 'Asset URL copied.' });
                        } catch {
                          toast({
                            title: 'Copy failed',
                            description: 'Could not copy URL.',
                            variant: 'destructive',
                          });
                        }
                      }}
                      className="p-1.5 rounded hover:bg-muted transition-colors"
                      title="Copy URL"
                    >
                      <Copy className="w-4 h-4" />
                    </button>
                    {(asset.url || asset.thumbnail) && (
                      <a
                        href={asset.url || asset.thumbnail}
                        download={asset.name}
                        target="_blank"
                        rel="noreferrer"
                        className="p-1.5 rounded hover:bg-muted transition-colors"
                        title="Download"
                      >
                        <Download className="w-4 h-4" />
                      </a>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleDelete(asset.id)}
                    className="p-1.5 rounded hover:bg-muted text-red-500 transition-colors"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-xl border bg-card overflow-hidden">
          <table className="w-full">
            <thead className="bg-muted/50 border-b">
              <tr>
                <th className="p-4 text-left">
                  <input
                    type="checkbox"
                    onChange={() => {
                      if (selectedAssets.length === filteredAssets.length) {
                        setSelectedAssets([]);
                      } else {
                        setSelectedAssets(filteredAssets.map((a) => a.id));
                      }
                    }}
                    checked={
                      selectedAssets.length === filteredAssets.length && filteredAssets.length > 0
                    }
                    className="rounded"
                  />
                </th>
                <th className="p-4 text-left text-sm font-medium">{t('assets.name')}</th>
                <th className="p-4 text-left text-sm font-medium">{t('assets.type')}</th>
                <th className="p-4 text-left text-sm font-medium">{t('assets.status')}</th>
                <th className="p-4 text-right text-sm font-medium">{t('assets.impressions')}</th>
                <th className="p-4 text-right text-sm font-medium">CTR</th>
                <th className="p-4 text-right text-sm font-medium">{t('assets.actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {filteredAssets.map((asset) => (
                <tr key={asset.id} className="hover:bg-muted/30 transition-colors">
                  <td className="p-4">
                    <input
                      type="checkbox"
                      checked={selectedAssets.includes(asset.id)}
                      onChange={() => toggleSelectAsset(asset.id)}
                      className="rounded"
                    />
                  </td>
                  <td className="p-4">
                    <button
                      type="button"
                      onClick={() => openPreview(asset)}
                      className="flex items-center gap-3 text-left hover:opacity-90"
                    >
                      {asset.thumbnail ? (
                        <img
                          src={asset.thumbnail}
                          alt={asset.name}
                          className="w-12 h-12 rounded object-cover"
                        />
                      ) : (
                        <div className="w-12 h-12 rounded bg-muted flex items-center justify-center">
                          {getTypeIcon(asset.type)}
                        </div>
                      )}
                      <div>
                        <p className="font-medium text-sm">{asset.name}</p>
                        <p className="text-xs text-muted-foreground">
                          {asset.campaigns.length > 0
                            ? `${asset.campaigns.length} campaigns`
                            : 'Not assigned'}
                        </p>
                      </div>
                    </button>
                  </td>
                  <td className="p-4">
                    <div className="flex items-center gap-2 text-sm capitalize">
                      {getTypeIcon(asset.type)}
                      <span>{asset.type}</span>
                    </div>
                  </td>
                  <td className="p-4">{getStatusBadge(asset.status)}</td>
                  <td className="p-4 text-right font-medium">
                    {formatCompactNumber(asset.impressions)}
                  </td>
                  <td className="p-4 text-right font-medium">{formatPercent(asset.ctr)}</td>
                  <td className="p-4 text-right">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        type="button"
                        onClick={() => openPreview(asset)}
                        className="p-2 rounded hover:bg-muted transition-colors"
                      >
                        <Eye className="w-4 h-4" />
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDelete(asset.id)}
                        className="p-2 rounded hover:bg-muted text-red-500 transition-colors"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!isLoading && !isError && filteredAssets.length === 0 && (
        <div className="text-center py-12 space-y-3">
          <ImageIcon className="w-12 h-12 mx-auto text-muted-foreground" />
          <p className="text-muted-foreground">
            {assets.length === 0
              ? 'No assets yet. Upload your first creative to get started.'
              : t('assets.noResults')}
          </p>
          {assets.length === 0 && (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90"
            >
              <Upload className="w-4 h-4" />
              Upload asset
            </button>
          )}
        </div>
      )}

      {previewAsset && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60" onClick={() => setPreviewAsset(null)} />
          <div className="relative z-10 w-full max-w-3xl rounded-2xl border bg-card shadow-xl overflow-hidden">
            <div className="flex items-start justify-between gap-4 p-4 border-b">
              <div className="min-w-0 flex-1">
                {isEditing ? (
                  <div className="flex items-center gap-2">
                    <input
                      value={editingName}
                      onChange={(e) => setEditingName(e.target.value)}
                      className="w-full px-3 py-1.5 rounded-lg border bg-background"
                      autoFocus
                    />
                    <button
                      type="button"
                      onClick={() => void handleSaveName()}
                      disabled={updateAsset.isPending}
                      className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-sm disabled:opacity-50"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setIsEditing(false);
                        setEditingName(previewAsset.name);
                      }}
                      className="px-3 py-1.5 rounded-lg border text-sm"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="flex items-center gap-2">
                      <h2 className="font-semibold truncate">{previewAsset.name}</h2>
                      <button
                        type="button"
                        onClick={() => setIsEditing(true)}
                        className="p-1 rounded hover:bg-muted"
                        title="Edit name"
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                    </div>
                    <p className="text-xs text-muted-foreground capitalize">
                      {previewAsset.type} · {previewAsset.status}
                    </p>
                  </>
                )}
              </div>
              <button
                type="button"
                onClick={() => setPreviewAsset(null)}
                className="p-2 rounded-lg border hover:bg-muted"
                aria-label="Close"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="bg-muted flex items-center justify-center min-h-[280px]">
              {previewAsset.type === 'video' ? (
                <video
                  src={previewAsset.url || previewAsset.thumbnail}
                  controls
                  className="max-h-[70vh] w-full"
                />
              ) : previewAsset.thumbnail || previewAsset.url ? (
                <img
                  src={previewAsset.thumbnail || previewAsset.url}
                  alt={previewAsset.name}
                  className="max-h-[70vh] w-full object-contain"
                />
              ) : (
                <div className="text-muted-foreground py-16">No preview available</div>
              )}
            </div>

            <div className="flex items-center justify-between gap-3 p-4 border-t">
              <div className="text-sm text-muted-foreground">
                Impressions {formatCompactNumber(previewAsset.impressions)} · CTR{' '}
                {formatPercent(previewAsset.ctr)}
              </div>
              <div className="flex gap-2">
                {(previewAsset.url || previewAsset.thumbnail) && (
                  <a
                    href={previewAsset.url || previewAsset.thumbnail}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border hover:bg-muted text-sm"
                  >
                    <Download className="w-4 h-4" />
                    Open file
                  </a>
                )}
                <button
                  type="button"
                  onClick={() => void handleDelete(previewAsset.id)}
                  className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-red-500/10 text-red-500 hover:bg-red-500/20 text-sm"
                >
                  <Trash2 className="w-4 h-4" />
                  Delete
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default Assets;
