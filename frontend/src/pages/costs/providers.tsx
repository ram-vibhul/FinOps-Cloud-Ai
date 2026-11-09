import { CircleAlert } from "lucide-react";
import { Link } from "react-router";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, type CostMetric } from "@/lib/api";
import { money, percent, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/use-load";

/** Every cloud and SaaS source side by side in one currency (2.16). */
export function ProvidersCard({ metric, currency, org }: { metric: CostMetric; currency?: string; org: string | null }) {
  const { data } = useLoad(() => api.costProviders({ metric, currency }), [org, metric, currency]);
  if (!data || data.providers.length < 2) return null; // one source: the page already shows it
  return (
    <Card>
      <CardHeader>
        <CardTitle>All clouds and services</CardTitle>
        <CardDescription>
          This month to {shortDate(data.data_through)}, in {data.currency}; change against the same days last month.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <THead>
            <TR>
              <TH>Source</TH><TH className="text-right">This month</TH><TH className="text-right">Change</TH>
              <TH className="text-right">Last month</TH><TH className="w-32">Share</TH><TH>Data</TH>
            </TR>
          </THead>
          <TBody>
            {data.providers.map((p) => {
              const broken = p.connections.filter((c) => c.status === "error");
              return (
                <TR key={p.provider}>
                  <TD className="font-medium">{p.name}</TD>
                  <TD className="text-right tabular-nums">{money(p.month_to_date, data.currency)}</TD>
                  <TD className="text-right tabular-nums">{percent(p.change_pct)}</TD>
                  <TD className="text-right tabular-nums">{money(p.last_month, data.currency)}</TD>
                  <TD>
                    <meter className="w-full" min={0} max={100} value={p.share_pct} aria-label={`${p.name}: ${p.share_pct}% of spend`} />
                  </TD>
                  <TD className="text-sm text-muted-foreground">
                    {broken.length > 0 ? (
                      <Link to="/settings?tab=clouds" className="inline-flex items-center gap-1 text-destructive underline underline-offset-4">
                        <CircleAlert className="size-4" aria-hidden /> Connection problem
                      </Link>
                    ) : p.data_through ? (
                      `to ${shortDate(p.data_through)}`
                    ) : (
                      "waiting for the first import"
                    )}
                  </TD>
                </TR>
              );
            })}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}
