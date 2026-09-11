/**
 * Knowledge Graph Insights - Overview of graph-derived revenue insights.
 *
 * Placeholder view: links out to the problem-detection and revenue-attribution
 * knowledge graph views while the full insights UI is under construction.
 */

import { Link } from 'react-router-dom';
import { AlertTriangle, DollarSign, Network, Share2 } from 'lucide-react';

export default function KnowledgeGraphInsights() {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Network className="w-6 h-6 text-primary" />
          Knowledge Graph Insights
        </h1>
        <p className="text-muted-foreground mt-1">
          Cross-entity intelligence connecting campaigns, audiences, creatives and revenue
        </p>
      </div>

      {/* Quick links */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Link
          to="/dashboard/knowledge-graph/problems"
          className="p-6 rounded-xl border bg-card hover:shadow-md transition-all flex items-start gap-4"
        >
          <div className="p-3 rounded-lg bg-amber-500/10 text-amber-500">
            <AlertTriangle className="w-6 h-6" />
          </div>
          <div>
            <h3 className="font-semibold mb-1">Problem Detection</h3>
            <p className="text-sm text-muted-foreground">
              Automatically detected issues across your campaign graph: signal drops, budget
              conflicts and creative fatigue.
            </p>
          </div>
        </Link>
        <Link
          to="/dashboard/knowledge-graph/revenue"
          className="p-6 rounded-xl border bg-card hover:shadow-md transition-all flex items-start gap-4"
        >
          <div className="p-3 rounded-lg bg-green-500/10 text-green-500">
            <DollarSign className="w-6 h-6" />
          </div>
          <div>
            <h3 className="font-semibold mb-1">Revenue Attribution</h3>
            <p className="text-sm text-muted-foreground">
              Trace revenue through the graph to see which campaigns, audiences and creatives
              actually drive it.
            </p>
          </div>
        </Link>
      </div>

      {/* Info card */}
      <div className="p-6 rounded-xl border bg-muted/30 flex items-start gap-4">
        <Share2 className="w-5 h-5 text-primary mt-0.5" />
        <div>
          <h3 className="font-medium mb-1">About the Knowledge Graph</h3>
          <p className="text-sm text-muted-foreground">
            Stratum builds a live graph of your Meta ad entities (campaigns, ad sets, ads,
            audiences, creatives) and connects them to CDP profiles and revenue events. Insights
            here are for operators. This portal release does not feed executing Autopilot writes.
          </p>
        </div>
      </div>
    </div>
  );
}
