import { Link } from 'react-router-dom';

interface LaunchUnavailableProps {
  title: string;
  reason: string;
}

/**
 * Honest stand-in for modules that are not part of this portal release.
 * Bookmarking the old URL must not load mock data that looks live.
 */
export default function LaunchUnavailable({ title, reason }: LaunchUnavailableProps) {
  return (
    <div className="max-w-xl mx-auto py-16 px-6 text-center">
      <p className="text-xs uppercase tracking-widest text-muted-foreground mb-3">
        Not in this release
      </p>
      <h1 className="text-2xl font-semibold mb-3">{title}</h1>
      <p className="text-sm text-muted-foreground mb-8">{reason}</p>
      <Link to="/dashboard/overview" className="text-sm text-primary hover:underline">
        Back to overview
      </Link>
    </div>
  );
}
