import { Download, Plus, Send, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage, type ReportScheduleRow } from "@/lib/api";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const KINDS = { cost_summary: "Cost summary", savings: "Savings", budgets: "Budgets", showback: "Showback", leadership: "Leadership one-pager" } as const;
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function when(s: ReportScheduleRow): string {
  if (s.frequency === "daily") return "every day";
  if (s.frequency === "weekly") return `every ${DAYS[s.weekday]}`;
  return `on day ${s.day} of each month`;
}

export function ReportsTab() {
  const me = useMe();
  const canEdit = atLeast(me.role, "member") && !activeOrg(me)?.is_demo;
  const list = useLoad(() => api.reportSchedules(), [me.active_organization_id]);
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ name: "", kind: "cost_summary" as keyof typeof KINDS, frequency: "monthly" as ReportScheduleRow["frequency"],
    weekday: 0, day: 1, format: "pdf" as "pdf" | "csv", recipients: "", dimension: "tag:team" });

  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      await api.createReportSchedule({ name: f.name.trim(), kind: f.kind, frequency: f.frequency, weekday: f.weekday, day: f.day,
        format: f.format, recipients: f.recipients.split(",").map((v) => v.trim()).filter(Boolean),
        params: f.kind === "showback" ? { dimension: f.dimension } : {} });
      toast.success("Report scheduled.");
      setAdding(false);
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function act(run: () => Promise<unknown>, done: string) {
    try {
      await run();
      toast.success(done);
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Reports</CardTitle>
        <CardDescription>Download a report now, or have it emailed as PDF or CSV on a schedule (sent from 07:00 UTC).</CardDescription>
        <div className="flex flex-wrap gap-2">
          {Object.entries(KINDS).map(([k, v]) => (
            <Button key={k} size="sm" variant="outline" asChild><a href={`/api/v1/reports/download?kind=${k}`} download><Download /> {v}</a></Button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        {canEdit && !adding && <div><Button onClick={() => setAdding(true)}><Plus /> Schedule a report</Button></div>}
        {adding && (
          <form onSubmit={(e) => void save(e)} aria-label="New report schedule" className="grid gap-3 rounded-lg border p-3 sm:grid-cols-4">
            <div className="grid gap-1"><Label htmlFor="rs-name">Name</Label>
              <Input id="rs-name" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
            <div className="grid gap-1"><Label htmlFor="rs-kind">Report</Label>
              <NativeSelect id="rs-kind" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as keyof typeof KINDS })}>
                {Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </NativeSelect></div>
            <div className="grid gap-1"><Label htmlFor="rs-freq">How often</Label>
              <NativeSelect id="rs-freq" value={f.frequency} onChange={(e) => setF({ ...f, frequency: e.target.value as ReportScheduleRow["frequency"] })}>
                <option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option>
              </NativeSelect></div>
            {f.frequency === "weekly" && (
              <div className="grid gap-1"><Label htmlFor="rs-weekday">On</Label>
                <NativeSelect id="rs-weekday" value={f.weekday} onChange={(e) => setF({ ...f, weekday: Number(e.target.value) })}>
                  {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
                </NativeSelect></div>
            )}
            {f.frequency === "monthly" && (
              <div className="grid gap-1"><Label htmlFor="rs-day">Day of month</Label>
                <Input id="rs-day" type="number" min={1} max={28} value={f.day} onChange={(e) => setF({ ...f, day: Number(e.target.value) })} /></div>
            )}
            <div className="grid gap-1"><Label htmlFor="rs-format">Format</Label>
              <NativeSelect id="rs-format" value={f.format} onChange={(e) => setF({ ...f, format: e.target.value as "pdf" | "csv" })}>
                <option value="pdf">PDF</option><option value="csv">CSV</option>
              </NativeSelect></div>
            {f.kind === "showback" && (
              <div className="grid gap-1"><Label htmlFor="rs-dim">Split by</Label>
                <Input id="rs-dim" value={f.dimension} onChange={(e) => setF({ ...f, dimension: e.target.value })} /></div>
            )}
            <div className="grid gap-1 sm:col-span-2"><Label htmlFor="rs-to">Send to (comma-separated)</Label>
              <Input id="rs-to" required value={f.recipients} onChange={(e) => setF({ ...f, recipients: e.target.value })} /></div>
            <div className="flex gap-2 sm:col-span-4">
              <Button type="submit">Save schedule</Button>
              <Button type="button" variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
            </div>
          </form>
        )}
        <ul className="divide-y" aria-label="Report schedules">
          {(list.data ?? []).map((s) => (
            <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span>
                <span className="font-medium">{s.name}</span> — {s.kind_label} ({s.format.toUpperCase()}) {when(s)} to {s.recipients.join(", ")}
                <span className="text-muted-foreground">{s.last_sent_at ? ` · last sent ${new Date(s.last_sent_at).toLocaleString()}` : " · not sent yet"}</span>
                {s.last_error && <span className="text-destructive"> · {s.last_error}</span>}
              </span>
              {canEdit && (
                <span className="flex gap-1">
                  <Button size="sm" variant="ghost" onClick={() => void act(() => api.sendReportNow(s.id), "Sent.")}><Send /> Send now</Button>
                  <Button size="icon" variant="ghost" aria-label={`Delete ${s.name}`} onClick={() => void act(() => api.deleteReportSchedule(s.id), "Deleted.")}><Trash2 /></Button>
                </span>
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
