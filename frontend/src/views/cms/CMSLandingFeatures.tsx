/**
 * CMS Landing Features - Manage feature cards shown on the landing page (placeholder).
 */

import { LayoutGrid } from 'lucide-react';

export default function CMSLandingFeatures() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Landing Features</h1>
        <p className="text-muted-foreground mt-1">
          Manage the feature cards displayed on the public landing page
        </p>
      </div>

      <div className="rounded-xl border bg-card p-12 text-center">
        <LayoutGrid className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
        <h3 className="font-semibold mb-2">Feature editor coming soon</h3>
        <p className="text-sm text-muted-foreground max-w-md mx-auto">
          This section will let you edit feature card titles, descriptions and ordering. Feature
          content is currently managed in the landing page configuration.
        </p>
      </div>
    </div>
  );
}
