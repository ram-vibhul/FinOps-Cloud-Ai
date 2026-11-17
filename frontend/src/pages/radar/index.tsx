import { Radar as RadarIcon } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, type RadarFeed, type RadarUndo } from "@/lib/api";
import { money } from "@/lib/format";
import { useMe } from "@/lib/session";
import { timeAgo } from "@/lib/time-ago";
import { useLoad } from "@/lib/use-load";
import { usePoll } from "@/lib/use-poll";

const REFRESH_MS = 60_000;
const WINDOWS = [
  { hours: 24, label: "Last 24 hours" },
  { hours: 72, label: "Last 3 days" },
  { hours: 168, label: "Last 7 days" },
];

function signed(value: number, currency: string): string {
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${money(Math.abs(value), currency)}`;
}

function Tile({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <Card>
      <CardContent className="pt-1">
        <p className="text-sm text-muted-foreground">{label}</p>
        <p className="text-2xl font-semibold tabular-nums">{value} <span className="text-sm font-normal text-muted-foreground">/ month</span></p>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </CardContent>
    </Card>
  );
}

function copy(text: string) {
  void navigator.clipboard?.writeText(text).then(
    () => toast.success("Command copied"),
    () => toast.error("Could not copy; select the text instead"),
  );
}

/** How to reverse a change (11.30). Shown for a person to check and run: CloudCostRadar never runs it. */
function UndoHelp({ undo }: { undo: RadarUndo }) {
  return (
    <details className="mt-1 text-xs">
      <summary className="cursor-pointer text-muted-foreground underline-offset-4 hover:underline">How to undo</summary>
      <div className="mt-1 flex flex-col gap-1">
        <p>{undo.text}</p>
        {undo.command && (
          <div className="flex items-start gap-2">
            <pre className="max-w-xl overflow-x-auto rounded bg-muted px-2 py-1 font-mono text-[11px]">{undo.command}</pre>
            <Button type="button" size="sm" variant="outline" onClick={() => copy(undo.command ?? "")}>Copy</Button>
          </div>
        )}
        {undo.console && (
          <a href={undo.console} target="_blank" rel="noreferrer" className="underline underline-offset-4">Open in the AWS console</a>
        )}
        <p className="text-muted-foreground">Check it first: CloudCostRadar only reads your account and never runs this for you.</p>
      </div>
    </details>
  );
}

function Feed({ data, focus }: { data: RadarFeed; focus: string | null }) {
  return (
    <Table>
      <THead>
        <TR>
          <TH>When</TH><TH>Who</TH><TH>What changed</TH><TH>Where</TH>
          <TH className="text-right">Run rate, a month</TH><TH><span className="sr-only">In the bill?</span></TH>
        </TR>
      </THead>
      <TBody>
        {data.items.map((i) => (
          <TR key={i.id} className={i.id === focus ? "bg-primary/10" : undefined} aria-current={i.id === focus ? "true" : undefined}>
            <TD className="whitespace-nowrap text-sm">{timeAgo(i.occurred_at)}</TD>
            <TD>{i.actor}</TD>
            <TD>
              <div className="font-medium">{i.label ?? i.title}</div>
              <div className="text-xs text-muted-foreground">
                {i.title}{i.service ? ` · ${i.service}` : ""} · priced from {i.source === "bill" ? "your own rates" : "list prices"}
              </div>
              {i.measured && (
                <div className="text-xs text-muted-foreground">
                  Measured in the bill: {money(i.measured.monthly, data.currency)} a month per resource over {i.measured.days} days
                  {i.measured.error_pct !== null && ` (estimate ${i.measured.error_pct > 0 ? "under" : "over"} by ${Math.abs(i.measured.error_pct)}%)`}
                </div>
              )}
              {i.undo && <UndoHelp undo={i.undo} />}
            </TD>
            <TD className="text-sm">{[i.region, i.account].filter(Boolean).join(" · ") || "—"}</TD>
            <TD className={`text-right font-medium tabular-nums ${i.monthly > 0 ? "text-destructive" : "text-primary"}`}>
              {signed(i.monthly, data.currency)}
            </TD>
            <TD className="space-y-1">
              {i.in_bill ? <Badge variant="secondary">In the bill</Badge> : <Badge variant="warning">Not in the bill yet</Badge>}
              {i.policy?.map((h) => (
                <div key={h.policy}><Badge variant={h.severity === "critical" ? "destructive" : "warning"} title={h.detail}>Breaks {h.policy}</Badge></div>
              ))}
              {i.expected && <div><Badge variant="outline" title={`Marked by ${i.expected.by}${i.expected.note ? `: ${i.expected.note}` : ""}`}>Expected</Badge></div>}
            </TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

/** Live Radar (10.1): what infrastructure changes cost, before the bill shows them. */
export function RadarPage() {
  const me = useMe();
  const [hours, setHours] = useState(24);
  const { data, error, reload } = useLoad(() => api.radar(hours), [me.active_organization_id, hours]);
  usePoll(() => void reload(), REFRESH_MS);
  const [params, setParams] = useSearchParams();
  const focus = params.get("change");
  const asked = focus && params.get("expected") === "1" ? data?.items.find((i) => i.id === focus) : undefined;
  const answer = async (expected: boolean) => {
    if (expected && asked) {
      try {
        await api.radarExpected(asked.id, true);
        toast.success("Marked as expected");
        void reload();
      } catch (e) {
        toast.error(errorMessage(e));
      }
    }
    setParams({ change: focus ?? "" }, { replace: true });
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Radar</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            The price and owner of every costly change. Cloud bills arrive a day or two late; Radar prices each change
            minutes after it is made, at your own rates where it has them, so you see who launched what and what it adds
            to the monthly bill before it gets there.
          </p>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="radar-window">Show</Label>
          <NativeSelect id="radar-window" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            {WINDOWS.map((w) => <option key={w.hours} value={w.hours}>{w.label}</option>)}
          </NativeSelect>
        </div>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {asked && !asked.expected && (
        <div role="alertdialog" aria-label="Mark as expected" className="flex flex-wrap items-center gap-3 rounded-md border px-3 py-2 text-sm">
          <span>Mark <strong>{asked.label ?? asked.title}</strong> by {asked.actor} as expected?</span>
          <Button size="sm" onClick={() => void answer(true)}>Mark as expected</Button>
          <Button size="sm" variant="outline" onClick={() => void answer(false)}>Not now</Button>
        </div>
      )}
      {data?.scans?.filter((x) => !x.complete).map((x) => (
        <p key={x.connection} role="note" className="rounded-md border border-warning bg-warning/15 px-3 py-2 text-sm">
          Some changes in <strong>{x.connection}</strong> may not be shown yet: the last scan was cut short ({x.problems.join("; ")}).
          The next scan, within 10 minutes, picks up from the same point.
        </p>
      ))}
      {data && data.items.length === 0 && (
        <EmptyState icon={RadarIcon} title="No costly changes in this period">
          Radar reads the change history of your AWS accounts every 10 minutes, and of Azure subscriptions and Google Cloud
          projects every 15. Launches, resizes and deletions of servers, databases, disks, load balancers and gateways show
          here with their price.{" "}
          <Link to="/settings?tab=clouds" className="underline underline-offset-4">Connect an AWS account</Link> if you have not yet.
        </EmptyState>
      )}
      {data && data.items.length > 0 && (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <Tile label="Not in your bill yet" value={signed(data.not_in_bill_monthly, data.currency)}
              hint={data.billed_through ? `Changes made on or after ${data.billed_through}, the last day of billing data.` : "No billing data has arrived yet."} />
            <Tile label="Added" value={signed(data.added_monthly, data.currency)} hint="New and larger resources." />
            <Tile label="Removed" value={signed(data.removed_monthly, data.currency)} hint="Deleted, stopped and smaller resources." />
          </div>
          <Card>
            <CardHeader>
              <CardTitle>Changes and what they cost</CardTitle>
              <CardDescription>
                Estimates from each change's own details; the Costs timeline shows the measured effect once the bill arrives.
                {data.unpriced > 0 && ` ${data.unpriced} other change${data.unpriced === 1 ? "" : "s"} could not be priced.`}{" "}
                {data.accuracy && `Checked against the bill so far: ${data.accuracy.share_within}% of ${data.accuracy.measured} estimates were within ${data.accuracy.within_pct}% (median error ${data.accuracy.median_error_pct}%). `}
                Alerts are set under <Link to="/settings?tab=general" className="underline underline-offset-4">Settings</Link>.
              </CardDescription>
            </CardHeader>
            <CardContent><Feed data={data} focus={focus} /></CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
