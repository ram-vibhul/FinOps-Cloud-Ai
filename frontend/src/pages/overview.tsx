import { Gauge } from "lucide-react";
import { Link } from "react-router";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";
import { AnomaliesCard } from "./costs/anomalies";
import { SummaryTiles } from "./costs/summary-tiles";

export function OverviewPage() {
  const me = useMe();
  const summary = useLoad(() => api.costSummary(), [me.active_organization_id]);
  const meta = useLoad(() => api.meta(), []);
  const s = summary.data;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
        <p className="text-sm text-muted-foreground">Headline spend, trends and what needs your attention.</p>
      </div>
      {s?.data_through ? (
        <>
          <SummaryTiles summary={s} />
          <AnomaliesCard compact />
          <Button asChild variant="outline" className="self-start">
            <Link to="/costs">See where the money goes</Link>
          </Button>
        </>
      ) : summary.loading && !s ? null : (
        <EmptyState icon={Gauge} title="No cost data yet">
          Connect a cloud account or open the demo organisation to see where your money goes.
        </EmptyState>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Instance</CardTitle>
          <CardDescription>About this installation</CardDescription>
        </CardHeader>
        <CardContent className="text-sm" aria-live="polite">
          {meta.data ? (
            <span>
              {meta.data.product} {meta.data.version} &middot; {meta.data.deployment_mode?.replace("_", "-")} &middot;{" "}
              {meta.data.environment}
            </span>
          ) : meta.error ? (
            <span className="text-destructive">Could not reach the API.</span>
          ) : (
            <span className="text-muted-foreground">Loading&hellip;</span>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
