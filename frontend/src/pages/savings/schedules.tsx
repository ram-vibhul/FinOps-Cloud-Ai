import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage, type StopSchedule } from "@/lib/api";
import { atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function localZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function AddSchedule({ onAdded }: { onAdded: () => void }) {
  const connections = useLoad(() => api.connections(), []);
  const aws = (connections.data ?? []).filter((c) => c.provider === "aws");
  const [open, setOpen] = useState(false);
  const [days, setDays] = useState([0, 1, 2, 3, 4]);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const text = (k: string) => String(f.get(k) ?? "").trim();
    try {
      await api.requestSchedule({
        connection_id: text("connection") || aws[0]?.id || "", name: text("name"), region: text("region"),
        instance_ids: text("instances").split(/[\s,]+/).filter(Boolean), timezone: text("timezone"),
        days, stop_at: text("stop_at"), start_at: text("start_at"),
      });
      toast.success("Asked for approval. An administrator turns it on.");
      setOpen(false);
      onAdded();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button size="sm" variant="outline" disabled={!aws.length}>Add a schedule</Button></DialogTrigger>
      <DialogContent title="Stop servers outside office hours" description="An administrator approves the schedule before anything is stopped.">
        <form onSubmit={(e) => void submit(e)} className="grid gap-3" aria-label="Office-hours schedule">
          <div className="grid gap-1"><Label htmlFor="s-conn">AWS account</Label>
            <NativeSelect id="s-conn" name="connection">{aws.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</NativeSelect></div>
          <div className="grid gap-1"><Label htmlFor="s-name">Name</Label><Input id="s-name" name="name" required placeholder="Staging" /></div>
          <div className="grid gap-1"><Label htmlFor="s-region">Region</Label><Input id="s-region" name="region" required placeholder="ap-south-1" /></div>
          <div className="grid gap-1"><Label htmlFor="s-ids">Instance IDs</Label>
            <textarea id="s-ids" name="instances" required rows={3} className="rounded-md border bg-transparent px-3 py-2 font-mono text-sm" placeholder="i-0abc… i-0def…" /></div>
          <div className="grid grid-cols-3 gap-2">
            <div className="grid gap-1"><Label htmlFor="s-stop">Stop at</Label><Input id="s-stop" name="stop_at" type="time" defaultValue="20:00" required /></div>
            <div className="grid gap-1"><Label htmlFor="s-start">Start at</Label><Input id="s-start" name="start_at" type="time" defaultValue="08:00" required /></div>
            <div className="grid gap-1"><Label htmlFor="s-tz">Time zone</Label><Input id="s-tz" name="timezone" defaultValue={localZone()} required /></div>
          </div>
          <fieldset className="flex flex-wrap gap-3 text-sm"><legend className="mb-1 text-sm font-medium">Days</legend>
            {DAYS.map((d, i) => (
              <label key={d} className="flex items-center gap-1">
                <input type="checkbox" checked={days.includes(i)} onChange={(e) => setDays(e.target.checked ? [...days, i].toSorted() : days.filter((x) => x !== i))} />{d}
              </label>
            ))}
          </fieldset>
          <p className="text-xs text-muted-foreground">Servers tagged ccradar-protect or in an Auto Scaling group are never touched, and only servers CloudCostRadar stopped are started again.</p>
          <Button type="submit">Ask for approval</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Row({ s, isAdmin, onChange }: { s: StopSchedule; isAdmin: boolean; onChange: () => void }) {
  async function act(action: "approve" | "pause" | "resume" | "reject" | "delete") {
    try {
      if (action === "delete") await api.deleteSchedule(s.id);
      else await api.changeSchedule(s.id, action);
      onChange();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }
  const last = s.log[0];
  return (
    <li className="flex flex-wrap items-center gap-2 rounded-md border p-2 text-sm">
      <Badge variant={s.status === "approved" ? "default" : "outline"}>{s.status}</Badge>
      <span className="font-medium">{s.name}</span>
      <span className="text-muted-foreground">
        {s.instance_ids.length} server{s.instance_ids.length === 1 ? "" : "s"} in {s.region}, off {s.stop_at}–{s.start_at} ({s.timezone}),{" "}
        {s.days.map((d) => DAYS[d]).join(" ")}
      </span>
      {s.stopped_now.length > 0 && <Badge variant="warning">{s.stopped_now.length} stopped now</Badge>}
      {last && <span className="text-xs text-muted-foreground">Last: {last.action} {last.error ? `failed (${last.error})` : `${last.done?.length ?? 0} done`}</span>}
      {isAdmin && (
        <span className="ml-auto flex gap-1">
          {s.status === "requested" && <Button size="sm" onClick={() => void act("approve")}>Approve</Button>}
          {s.status === "requested" && <Button size="sm" variant="outline" onClick={() => void act("reject")}>Reject</Button>}
          {s.status === "approved" && <Button size="sm" variant="outline" onClick={() => void act("pause")}>Pause</Button>}
          {(s.status === "paused" || s.status === "rejected") && <Button size="sm" variant="outline" onClick={() => void act("resume")}>Ask again</Button>}
          <Button size="sm" variant="ghost" onClick={() => void act("delete")}>Delete</Button>
        </span>
      )}
    </li>
  );
}

/** Office-hours schedules (11.35): non-production servers stopped at night through the fixes role. */
export function SchedulesCard() {
  const me = useMe();
  const canAsk = atLeast(me.role, "member");
  const list = useLoad(() => api.schedules(), [me.active_organization_id]);
  if (!canAsk && !list.data?.length) return null;
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div>
          <CardTitle>Office hours</CardTitle>
          <CardDescription>Stop test and staging servers at night and at weekends, about 64% less for those servers. Needs automatic fixes turned on and an administrator's approval.</CardDescription>
        </div>
        {canAsk && <AddSchedule onAdded={() => void list.reload()} />}
      </CardHeader>
      <CardContent>
        {list.data?.length ? (
          <ul className="grid gap-2" aria-label="Office-hours schedules">
            {list.data.map((s) => <Row key={s.id} s={s} isAdmin={atLeast(me.role, "admin")} onChange={() => void list.reload()} />)}
          </ul>
        ) : <p className="text-sm text-muted-foreground">No schedules yet.</p>}
      </CardContent>
    </Card>
  );
}
