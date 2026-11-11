import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { api, type CloudConnection, errorMessage, type FlowLogSettings, type FlowLogSource } from "@/lib/api";

function label(s: FlowLogSource): string {
  return s.type === "s3" ? `S3: ${s.bucket}${s.prefix ? `/${s.prefix}` : ""}` : `CloudWatch Logs: ${s.log_group}`;
}

export function FlowLogsDialog({ connection }: { connection: CloudConnection }) {
  const [state, setState] = useState<FlowLogSettings | null>(null);
  const [found, setFound] = useState<FlowLogSource[] | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      setState(await api.flowLogs(connection.id));
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function step(run: () => Promise<void>) {
    setBusy(true);
    try {
      await run();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const sources = found ?? state?.sources ?? [];
  return (
    <Dialog onOpenChange={(open) => { if (open) void load(); }}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm">Flow logs</Button>
      </DialogTrigger>
      <DialogContent title={`VPC Flow Logs for ${connection.name}`}
        description="CloudCostRadar reads a day of flow records to show which workloads cause NAT, cross-zone and internet transfer cost. Read-only.">
        {state && (
          <div className="grid gap-3 text-sm">
            <p>{state.enabled ? "Attribution is on." : "Attribution is off."}
              {state.last_run && ` Last read ${new Date(state.last_run).toLocaleString()} (${state.lines ?? 0} records).`}</p>
            {Object.keys(state.errors).length > 0 && (
              <p role="alert" className="text-destructive">Could not read: {Object.entries(state.errors).map(([k, v]) => `${k} (${v})`).join(", ")}</p>
            )}
            <ul className="grid gap-1" aria-label="Flow log sources">
              {sources.map((s) => <li key={label(s)} className="font-mono text-xs">{label(s)}</li>)}
              {sources.length === 0 && <li className="text-muted-foreground">No sources yet: find them in the account.</li>}
            </ul>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" disabled={busy} onClick={() => void step(async () => {
                const r = await api.discoverFlowLogs(connection.id);
                setFound(r.sources);
                toast.success(`${r.sources.length} flow log destination(s) found.`);
              })}>Find flow logs</Button>
              <Button size="sm" disabled={busy || sources.length === 0} onClick={() => void step(async () => {
                setState(await api.saveFlowLogs(connection.id, { enabled: !state.enabled || found !== null, sources }));
                setFound(null);
                toast.success("Saved.");
              })}>{state.enabled && found === null ? "Turn off" : "Turn on with these sources"}</Button>
              {state.enabled && (
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => void step(async () => {
                  const r = await api.runFlowLogs(connection.id);
                  toast.success(`${r.lines} records read.`);
                  await load();
                })}>Read now</Button>
              )}
            </div>
            {state.policy_statements.length > 0 && (
              <div className="grid gap-1">
                <p className="font-medium">Add to the CloudCostRadar role so it can read these logs:</p>
                <pre className="max-h-40 overflow-auto rounded bg-muted p-2 text-xs">{JSON.stringify(state.policy_statements, null, 2)}</pre>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
