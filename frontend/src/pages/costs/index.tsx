import { ArrowDownRight, ArrowUpRight, ReceiptText } from "lucide-react";
import { Link } from "react-router";
import { Term } from "@/components/term";
import { useState } from "react";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, type CostDimension, type CostMetric, type CostMovers } from "@/lib/api";
import { money, percent, shortDate } from "@/lib/format";
import { useMe } from "@/lib/session";
import { CURRENCIES, useDisplayCurrency } from "@/lib/currency";
import { useLoad } from "@/lib/use-load";
import { AnomaliesCard } from "./anomalies";
import { HourlyCard } from "./hourly";
import { ProvidersCard } from "./providers";
import { StackedSpendChart } from "./charts";
import { SummaryTiles } from "./summary-tiles";
import { TimelineCard } from "./timeline";

const DIMENSIONS: { value: CostDimension; label: string }[] = [
  { value: "provider", label: "Cloud or service" },
  { value: "service", label: "Service" },
  { value: "account", label: "Account" },
  { value: "region", label: "Region" },
  { value: "charge_category", label: "Charge type" },
];

export function CostsPage() {
  const me = useMe();
  const [metric, setMetric] = useState<CostMetric>("effective");
  const [dimension, setDimension] = useState<CostDimension>("service");
  const [days, setDays] = useState(30);
  const [currency, setCurrency] = useDisplayCurrency();
  const org = me.active_organization_id;
  const summary = useLoad(() => api.costSummary({ metric, currency }), [org, metric, currency]);
  const daily = useLoad(() => {
    const end = summary.data?.data_through ?? undefined;
    const start = end ? new Date(new Date(`${end}T00:00:00Z`).getTime() - (days - 1) * 86400000).toISOString().slice(0, 10) : undefined;
    return api.costDaily({ metric, dimension, currency, start, end });
  }, [org, metric, dimension, days, currency, summary.data?.data_through]);
  const monthly = useLoad(() => api.costMonthly({ metric, currency, months: 12 }), [org, metric, currency]);
  const movers = useLoad(() => api.costMovers({ metric, dimension, currency, days: 7 }), [org, metric, dimension, currency]);

  const s = summary.data;
  const noData = s !== null && !s.data_through;
  const otherCurrencies = s ? Object.keys(s.other_currencies) : [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Costs</h1>
          <p className="text-sm text-muted-foreground">
            {s?.data_through ? `Data through ${shortDate(s.data_through)}.` : "Where your cloud money goes."}{" "}
            {metric === "effective"
              ? <><Term id="amortised">Amortised</Term>: discounts and commitments spread over the period.</>
              : <><Term id="billed">Billed</Term>: as the invoice shows.</>}{" "}
            Other currencies are converted with daily European Central Bank reference rates.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <Button asChild variant="outline">
            <Link to="/costs/explore">Open explorer</Link>
          </Button>
          <Button asChild variant="ghost">
            <Link to="/costs/resources">Resources</Link>
          </Button>
          <Button asChild variant="ghost">
            <Link to="/costs/data-quality">Data quality</Link>
          </Button>
          <Button asChild variant="ghost">
            <Link to="/costs/virtual-tags">Virtual tags</Link>
          </Button>
          <Button asChild variant="ghost">
            <Link to="/kubernetes">Kubernetes</Link>
          </Button>
          <Button asChild variant="ghost">
            <Link to="/costs/network">Network</Link>
          </Button>
          <Button asChild variant="ghost">
            <Link to="/costs/ai">AI spend</Link>
          </Button>
          <div className="grid gap-1">
            <Label htmlFor="cost-metric">Show</Label>
            <NativeSelect id="cost-metric" value={metric} onChange={(e) => setMetric(e.target.value as CostMetric)}>
              <option value="effective">Amortised cost</option>
              <option value="billed">Billed cost</option>
            </NativeSelect>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="cost-dimension">Group by</Label>
            <NativeSelect id="cost-dimension" value={dimension} onChange={(e) => setDimension(e.target.value as CostDimension)}>
              {DIMENSIONS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
            </NativeSelect>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="cost-currency">Currency</Label>
            <NativeSelect id="cost-currency" value={currency ?? ""} onChange={(e) => setCurrency(e.target.value || undefined)}>
              <option value="">Organisation default</option>
              {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </NativeSelect>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="cost-range">Range</Label>
            <NativeSelect id="cost-range" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
            </NativeSelect>
          </div>
        </div>
      </div>

      {noData ? (
        <EmptyState icon={ReceiptText} title="No cost data yet"
          action={<Button asChild><Link to="/settings?tab=clouds">Connect a cloud account</Link></Button>}>
          Connect AWS or upload a billing file. Or explore the demo organisation to see what this page looks like.
        </EmptyState>
      ) : (
        <>
          {otherCurrencies.length > 0 && s && (
            <p role="note" className="rounded-md border px-3 py-2 text-sm">
              Showing {s.currency} only. Spend in {otherCurrencies.join(", ")} is not converted yet:{" "}
              {otherCurrencies.map((code) => (
                <Button key={code} variant="link" className="h-auto p-0" onClick={() => setCurrency(code)}>
                  view {code}
                </Button>
              ))}
            </p>
          )}
          {s && <SummaryTiles summary={s} />}
          <ProvidersCard metric={metric} currency={currency} org={org} />
          <AnomaliesCard />
          <Card>
            <CardHeader>
              <CardTitle>Daily spend by {DIMENSIONS.find((d) => d.value === dimension)?.label.toLowerCase()}</CardTitle>
              <CardDescription>Largest seven groups; the rest are combined as Other.</CardDescription>
            </CardHeader>
            <CardContent>{daily.data && <StackedSpendChart data={daily.data} />}</CardContent>
          </Card>
          {s && <TimelineCard currency={s.currency} />}
          <HourlyCard />
          <div className="grid gap-6 xl:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Monthly spend</CardTitle>
                <CardDescription>Dashed: forecast for the rest of this month.</CardDescription>
              </CardHeader>
              <CardContent>
                {monthly.data && s && (
                  <StackedSpendChart data={monthly.data} monthly
                    forecastRemainder={Math.max(0, s.forecast.amount - s.month_to_date)} />
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Biggest changes this week</CardTitle>
                <CardDescription>Last 7 days compared with the 7 days before (purchases and tax excluded).</CardDescription>
              </CardHeader>
              <CardContent>{movers.data && <MoversTable data={movers.data} />}</CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

function MoversTable({ data }: { data: CostMovers }) {
  if (data.items.length === 0) return <p className="text-sm text-muted-foreground">No notable changes.</p>;
  return (
    <Table>
      <THead><TR><TH>What</TH><TH className="text-right">Last 7 days</TH><TH className="text-right">Change</TH></TR></THead>
      <TBody>
        {data.items.map((m) => {
          const up = m.change > 0;
          const Icon = up ? ArrowUpRight : ArrowDownRight;
          return (
            <TR key={m.key}>
              <TD className="max-w-40 truncate sm:max-w-64" title={m.key}>{m.key}</TD>
              <TD className="text-right tabular-nums">{money(m.current, data.currency)}</TD>
              <TD className="text-right tabular-nums">
                <span className="inline-flex items-center gap-1">
                  <Icon className="size-3.5" aria-hidden />
                  {up ? "+" : ""}{money(m.change, data.currency)}
                  <span className="text-xs text-muted-foreground">
                    ({m.status === "new" ? "new" : m.status === "gone" ? "stopped" : percent(m.change_pct)})
                  </span>
                </span>
              </TD>
            </TR>
          );
        })}
      </TBody>
    </Table>
  );
}
