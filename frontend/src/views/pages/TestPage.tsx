/**
 * TestPage - Simple public page used to verify routing works.
 */

export default function TestPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <div className="text-center p-8 rounded-xl border bg-card max-w-md">
        <h1 className="text-2xl font-bold mb-2">Test Page</h1>
        <p className="text-muted-foreground">
          Routing is working. This page exists only for debugging navigation.
        </p>
      </div>
    </div>
  );
}
