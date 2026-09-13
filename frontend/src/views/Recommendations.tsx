import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, Loader2, Sparkles } from 'lucide-react';
import { RecommendationsCard } from '@/views/dashboard/widgets/RecommendationsCard';
import {
  useApproveRecommendation,
  useDashboardRecommendations,
  useRejectRecommendation,
} from '@/api/dashboard';

export default function Recommendations() {
  const navigate = useNavigate();
  const { data, isLoading, refetch } = useDashboardRecommendations({ limit: 50 });
  const approveRecommendation = useApproveRecommendation();
  const rejectRecommendation = useRejectRecommendation();

  const recommendations = useMemo(() => data?.recommendations ?? [], [data]);

  const handleApprove = async (id: string) => {
    await approveRecommendation.mutateAsync(id);
    await refetch();
  };

  const handleReject = async (id: string) => {
    await rejectRecommendation.mutateAsync(id);
    await refetch();
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => navigate('/dashboard/overview')}
          className="p-2 rounded-lg border hover:bg-muted transition-colors"
          aria-label="Back to overview"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div>
          <div className="flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-primary" />
            <h1 className="text-2xl font-bold">AI Recommendations</h1>
          </div>
          <p className="text-muted-foreground">
            Approve or dismiss suggestions generated from your Meta campaign performance.
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center min-h-[240px]">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <RecommendationsCard
          recommendations={recommendations}
          loading={false}
          onApprove={handleApprove}
          onReject={handleReject}
        />
      )}
    </div>
  );
}
