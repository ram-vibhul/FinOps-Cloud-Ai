import { Network } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { EmptyState } from "@/components/empty-state";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, type CostSeries } from "@/lib/api";
import { useDisplayCurrency } from "@/lib/currency";
import { money } from "@/lib/format";
import { useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";
import { StackedSpendChart } from "./charts";

function Attribution() {
  const data = useLoad(() => api.networkAttribution(), []);
  const [category, setCategory] = useState("");
  const d = data.data;
  if (!d || d.items.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Who causes it</CardTitle>
          <CardDescription>
            Turn on VPC Flow Logs attribution (Settings → Cloud accounts → Flow logs) to see which workloads, instances and
            teams cause NAT, cross-zone and internet transfer cost.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }
  const cur = d.currency ?? "USD";
  const items = d.items.filter((i) => !category || i.category === category);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Who causes it</CardTitle>
        <CardDescription>
          From a day of VPC Flow Logs ({d.computed_at ? new Date(d.computed_at).toLocaleDateString() : ""}); each kind's
          billed monthly cost is shared out by bytes.
        </CardDescription>
        <div className="flex flex-wrap gap-2">
          <NativeSelect aria-label="Kind of transfer" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All kinds</option>
            {Object.entries(d.categories).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </NativeSelect>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4">
        <p className="text-sm" aria-label="By team">
          {d.teams.slice(0, 6).map((t) => `${t.team}: ${money(t.cost_monthly, cur)}`).join(" · ")}
        </p>
        <Table>
          <THead><TR><TH>Kind</TH><TH>Caused by</TH><TH>Team</TH><TH className="text-right">GB in sample</TH><TH className="text-right">Share</TH><TH className="text-right">Per month</TH></TR></THead>
          <TBody>
            {items.slice(0, 50).map((i) => (
              <TR key={`${i.category}/${i.owner}`}>
                <TD>{d.categories[i.category] ?? i.category}</TD>
                <TD className="max-w-64 truncate" title={i.owner}>{i.owner}</TD>
                <TD>{i.team ?? "–"}</TD>
                <TD className="text-right tabular-nums">{i.gb.toFixed(2)}</TD>
                <TD className="text-right tabular-nums">{i.share_pct.toFixed(1)}%</TD>
                <TD className="text-right tabular-nums">{money(i.cost_monthly, cur)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}

export function NetworkCostsPage() {
  const me = useMe();
  const [currency] = useDisplayCurrency();
  const [days, setDays] = useState(30);
  const data = useLoad(() => api.networkCosts(days, currency), [me.active_organization_id, days, currency]);
  const d = data.data;
  const series = useMemo<CostSeries | null>(() => (d ? {
    currency: d.currency, periods: d.daily.periods, total: d.total,
    series: d.daily.series.map((s) => ({ key: s.key, label: s.label, total: s.total, values: s.values })),
  } : null), [d]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Network costs</h1>
          <p className="text-sm text-muted-foreground">
            Data transfer from the bill: between zones, between regions, to the internet and through NAT gateways.{" "}
            <Link to="/costs" className="underline underline-offset-4">Back to costs</Link>
          </p>
        </div>
        <NativeSelect aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </NativeSelect>
      </div>
      {d && d.total === 0 && !data.loading ? (
        <EmptyState icon={Network} title="No data-transfer costs found">
          Transfer costs appear when the bill has usage types (the AWS billing export gives the most detail).
        </EmptyState>
      ) : d ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Card><CardHeader><CardDescription>All data transfer</CardDescription>
              <CardTitle className="text-2xl tabular-nums">{money(d.total, d.currency)}</CardTitle></CardHeader></Card>
            {d.categories.slice(0, 3).map((c) => (
              <Card key={c.key}><CardHeader><CardDescription>{c.label}</CardDescription>
                <CardTitle className="text-2xl tabular-nums">{money(c.cost, d.currency)}
                  <span className="text-base font-normal text-muted-foreground"> ({c.share_pct ?? 0}%)</span></CardTitle></CardHeader></Card>
            ))}
          </div>
          <Card>
            <CardHeader><CardTitle>Per day</CardTitle></CardHeader>
            <CardContent>{series && <StackedSpendChart data={series} />}</CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Who pays for it</CardTitle>
              <CardDescription>
                The bill shows where transfer is charged. Which traffic could take a cheaper path (for example S3 through a free
                gateway endpoint) needs VPC Flow Logs; the Savings page lists what to check.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <THead><TR><TH>Kind</TH><TH>Resource</TH><TH>Service</TH><TH>Account / region</TH><TH className="text-right">Cost</TH></TR></THead>
                <TBody>
                  {d.top.map((t) => (
                    <TR key={`${t.category}/${t.resource_id}/${t.account}/${t.region}/${t.service}`}>
                      <TD>{t.label}</TD>
                      <TD className="max-w-56 truncate" title={t.resource_id ?? ""}>{t.resource_id ?? "–"}</TD>
                      <TD>{t.service}</TD>
                      <TD className="text-muted-foreground">{[t.account, t.region].filter(Boolean).join(" / ")}</TD>
                      <TD className="text-right tabular-nums">{money(t.cost, d.currency)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </CardContent>
          </Card>
          <Attribution />
        </>
      ) : null}
    </div>
  );
}
