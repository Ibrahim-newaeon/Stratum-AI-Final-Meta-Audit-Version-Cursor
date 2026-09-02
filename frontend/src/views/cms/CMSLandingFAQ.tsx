/**
 * CMS Landing FAQ - Manage FAQ entries shown on the landing page (placeholder).
 */

import { HelpCircle } from 'lucide-react';

export default function CMSLandingFAQ() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Landing FAQ</h1>
        <p className="text-muted-foreground mt-1">
          Manage the questions and answers shown in the landing page FAQ section
        </p>
      </div>

      <div className="rounded-xl border bg-card p-12 text-center">
        <HelpCircle className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
        <h3 className="font-semibold mb-2">FAQ editor coming soon</h3>
        <p className="text-sm text-muted-foreground max-w-md mx-auto">
          This section will let you add, reorder and publish FAQ entries without a code change.
          FAQ content is currently managed in the landing page configuration.
        </p>
      </div>
    </div>
  );
}
