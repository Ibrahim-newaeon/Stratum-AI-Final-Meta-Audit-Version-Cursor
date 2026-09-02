/**
 * CMS Post Editor - Create or edit a blog post (placeholder).
 */

import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, FileEdit } from 'lucide-react';

export default function CMSPostEditor() {
  const { id } = useParams<{ id: string }>();
  const isEditing = Boolean(id);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link to="/cms/posts" className="p-2 rounded-lg hover:bg-muted transition-colors">
          <ArrowLeft className="w-5 h-5" />
        </Link>
        <div>
          <h1 className="text-2xl font-bold">{isEditing ? 'Edit Post' : 'New Post'}</h1>
          <p className="text-muted-foreground mt-1">
            {isEditing ? `Editing post ${id}` : 'Draft a new blog post'}
          </p>
        </div>
      </div>

      <div className="rounded-xl border bg-card p-12 text-center">
        <FileEdit className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
        <h3 className="font-semibold mb-2">Post editor coming soon</h3>
        <p className="text-sm text-muted-foreground max-w-md mx-auto mb-4">
          The rich-text post editor is being restored. In the meantime you can manage existing
          posts from the posts list.
        </p>
        <Link
          to="/cms/posts"
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Posts
        </Link>
      </div>
    </div>
  );
}
