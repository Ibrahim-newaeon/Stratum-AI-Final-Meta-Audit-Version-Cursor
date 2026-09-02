/**
 * CMS Contacts - Contact form submissions inbox (placeholder).
 */

import { Inbox } from 'lucide-react';

export default function CMSContacts() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Contact Submissions</h1>
        <p className="text-muted-foreground mt-1">
          Messages submitted through the public contact form
        </p>
      </div>

      <div className="rounded-xl border bg-card p-12 text-center">
        <Inbox className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
        <h3 className="font-semibold mb-2">No contact submissions yet</h3>
        <p className="text-sm text-muted-foreground max-w-md mx-auto">
          When visitors submit the contact form on the landing page, their messages will appear
          here for triage and follow-up.
        </p>
      </div>
    </div>
  );
}
