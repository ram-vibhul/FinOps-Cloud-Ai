import { Gauge } from "lucide-react";
import { useEffect, useState } from "react";
import { EmptyState } from "@/components/empty-state";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, type InstanceMeta } from "@/lib/api";

export function OverviewPage() {
  const [meta, setMeta] = useState<InstanceMeta | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .meta()
      .then((m) => !cancelled && setMeta(m))
      .catch(() => !cancelled && setError(true));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
        <p className="text-sm text-muted-foreground">Headline spend, trends and what needs your attention.</p>
      </div>
      <EmptyState icon={Gauge} title="No cost data yet">
        Connect a cloud account or open the demo organisation to see where your money goes.
      </EmptyState>
      <Card>
        <CardHeader>
          <CardTitle>Instance</CardTitle>
          <CardDescription>About this installation</CardDescription>
        </CardHeader>
        <CardContent className="text-sm" aria-live="polite">
          {meta ? (
            <span>
              {meta.product} {meta.version} &middot; {meta.deployment_mode.replace("_", "-")} &middot; {meta.environment}
            </span>
          ) : error ? (
            <span className="text-destructive">Could not reach the API.</span>
          ) : (
            <span className="text-muted-foreground">Loading&hellip;</span>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
