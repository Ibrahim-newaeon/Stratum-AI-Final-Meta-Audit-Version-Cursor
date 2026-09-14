/**
 * Studio Workspace overview — layout from Pearl/Indigo & Navy/Periwinkle boards.
 */

import { Link } from 'react-router-dom';
import {
  FileText,
  Folder,
  Image as ImageIcon,
  Link2,
  MoreHorizontal,
  UploadCloud,
  Users,
} from 'lucide-react';
import { useActivationStatus } from '@/api/activation';

const projects = [
  {
    name: 'Meta Ads',
    category: 'Campaigns',
    meta: 'Discover & sync',
    href: '/dashboard/campaigns',
  },
  {
    name: 'Audiences',
    category: 'CDP sync',
    meta: 'Custom Audiences',
    href: '/dashboard/cdp/audience-sync',
  },
  {
    name: 'Automation',
    category: 'Rules',
    meta: 'Trust-gated',
    href: '/dashboard/rules',
  },
  {
    name: 'Messaging',
    category: 'WhatsApp',
    meta: 'Inbox & templates',
    href: '/dashboard/whatsapp',
  },
];

const recent = [
  { name: 'Meta Setup checklist', type: 'Page', updated: 'Just now', owner: 'You', icon: FileText, href: '/dashboard/activation' },
  { name: 'Campaigns overview', type: 'Workspace', updated: 'Today', owner: 'You', icon: Folder, href: '/dashboard/campaigns' },
  { name: 'Asset library', type: 'Assets', updated: 'Today', owner: 'You', icon: ImageIcon, href: '/dashboard/assets' },
  { name: 'CDP segments', type: 'Segment', updated: 'Recently', owner: 'You', icon: Users, href: '/dashboard/cdp/segments' },
  { name: 'CAPI setup', type: 'Integration', updated: 'Recently', owner: 'You', icon: Link2, href: '/dashboard/capi-setup' },
];

export default function WorkspaceOverview() {
  const { data: activation } = useActivationStatus();
  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });

  return (
    <div className="mx-auto max-w-[1200px] space-y-8 px-6 py-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight" style={{ color: 'var(--studio-text)' }}>
            Studio workspace
          </h1>
          <p className="mt-2 text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
            Your campaigns, audiences, and assets — connected through Meta Setup.
          </p>
        </div>
        <div className="text-right text-sm" style={{ color: 'var(--studio-text-secondary)' }}>
          <div>{today}</div>
          <div className="mt-1 font-medium" style={{ color: 'var(--studio-text)' }}>
            {activation?.required_complete
              ? 'Meta integrations ready'
              : `Complete Meta Setup (${activation?.required_done ?? 0}/${activation?.required_total ?? 3})`}
          </div>
        </div>
      </div>

      {/* Project cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {projects.map((p) => (
          <Link
            key={p.name}
            to={p.href}
            className="studio-card group overflow-hidden transition-shadow hover:shadow-sm"
          >
            <div className="studio-art relative h-28">
              <button
                type="button"
                className="absolute right-2 top-2 rounded-lg bg-black/20 p-1.5 text-white opacity-0 transition group-hover:opacity-100"
                aria-label="More"
                onClick={(e) => e.preventDefault()}
              >
                <MoreHorizontal size={16} />
              </button>
            </div>
            <div className="space-y-1 p-4">
              <div className="font-semibold" style={{ color: 'var(--studio-text)' }}>
                {p.name}
              </div>
              <div className="text-xs" style={{ color: 'var(--studio-text-secondary)' }}>
                {p.category}
              </div>
              <div
                className="flex items-center gap-2 pt-2 text-xs"
                style={{ color: 'var(--studio-text-secondary)' }}
              >
                <span>{p.meta}</span>
              </div>
            </div>
          </Link>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Recent content */}
        <div className="studio-card lg:col-span-2">
          <div className="flex items-center justify-between border-b px-5 py-4" style={{ borderColor: 'var(--studio-border)' }}>
            <h2 className="text-sm font-semibold" style={{ color: 'var(--studio-text)' }}>
              Recent content
            </h2>
            <Link
              to="/dashboard/campaigns"
              className="text-xs font-medium"
              style={{ color: 'var(--studio-accent)' }}
            >
              View all →
            </Link>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr style={{ color: 'var(--studio-text-secondary)' }}>
                  <th className="px-5 py-3 font-medium">Name</th>
                  <th className="px-5 py-3 font-medium">Type</th>
                  <th className="px-5 py-3 font-medium">Last updated</th>
                  <th className="px-5 py-3 font-medium">Owner</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((row) => {
                  const Icon = row.icon;
                  return (
                    <tr key={row.name} className="border-t" style={{ borderColor: 'var(--studio-border)' }}>
                      <td className="px-5 py-3">
                        <Link to={row.href} className="flex items-center gap-2 font-medium" style={{ color: 'var(--studio-text)' }}>
                          <Icon size={16} style={{ color: 'var(--studio-accent)' }} />
                          {row.name}
                        </Link>
                      </td>
                      <td className="px-5 py-3" style={{ color: 'var(--studio-text-secondary)' }}>
                        {row.type}
                      </td>
                      <td className="px-5 py-3" style={{ color: 'var(--studio-text-secondary)' }}>
                        {row.updated}
                      </td>
                      <td className="px-5 py-3">
                        <span
                          className="inline-flex h-7 w-7 items-center justify-center rounded-full text-[10px] font-semibold"
                          style={{
                            background: 'var(--studio-nav-active)',
                            color: 'var(--studio-accent)',
                          }}
                        >
                          YO
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* Add new content / upload */}
        <div className="studio-card flex flex-col p-5">
          <h2 className="text-sm font-semibold" style={{ color: 'var(--studio-text)' }}>
            Add new content
          </h2>
          <div
            className="relative mt-4 flex flex-1 flex-col items-center justify-center rounded-[10px] border border-dashed px-4 py-10 text-center"
            style={{ borderColor: 'var(--studio-border)' }}
          >
            <UploadCloud size={28} style={{ color: 'var(--studio-accent)' }} />
            <p className="mt-3 text-sm font-medium" style={{ color: 'var(--studio-text)' }}>
              Drag and drop your files
            </p>
            <p className="mt-1 text-xs" style={{ color: 'var(--studio-text-secondary)' }}>
              Images, documents, or creative assets.
            </p>
            <div
              className="studio-art pointer-events-none absolute -right-2 top-6 h-14 w-20 rotate-6 rounded-lg border shadow-sm"
              style={{ borderColor: 'var(--studio-border)' }}
            />
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link to="/dashboard/assets" className="studio-btn-ghost flex-1 justify-center">
              Browse files
            </Link>
            <Link to="/dashboard/assets" className="studio-btn-primary flex-1 justify-center">
              <Link2 size={14} />
              Add from assets
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
