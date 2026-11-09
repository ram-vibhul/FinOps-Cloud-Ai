import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, type CloudConnection, errorMessage } from "@/lib/api";
import { useLoad } from "@/lib/use-load";

/** Owners turn automatic fixes on for one AWS account, with a separate role and limits. */
export function FixesDialog({ connection, isOwner }: { connection: CloudConnection; isOwner: boolean }) {
  const [open, setOpen] = useState(false);
  const status = useLoad(() => (open ? api.fixesStatus(connection.id) : Promise.resolve(null)), [open, connection.id]);
  const [roleArn, setRoleArn] = useState("");
  const [days, setDays] = useState(30);
  const [perDay, setPerDay] = useState(10);
  const [spend, setSpend] = useState(0);
  const [auto, setAuto] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const s = status.data;

  async function run(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      toast.success(ok);
      await status.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button variant="ghost" size="sm">Automatic fixes</Button></DialogTrigger>
      <DialogContent title="Automatic fixes (opt-in)"
        description="Off by default. When on, CostLens may stop idle instances, release idle Elastic IPs and delete unattached volumes (after a snapshot), but only after a second person approves each fix, and never on resources tagged costlens-protect = true.">
        {s?.enabled && s.settings ? (
          <div className="grid gap-2 text-sm">
            <p>On until <b>{new Date(s.settings.expires_at).toLocaleString()}</b>, at most {s.settings.max_actions_per_day} fix(es) a day, using <code className="break-all">{s.settings.role_arn}</code>.</p>
            <Button variant="destructive" className="justify-self-start" onClick={() => void run(() => api.disableFixes(connection.id), "Automatic fixes are off.")}>Turn off now</Button>
          </div>
        ) : (
          <div className="grid gap-3 text-sm">
            {s?.cloudformation && (
              <details><summary className="cursor-pointer">1. Create the separate {s.role_name} role (CloudFormation)</summary>
                <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-muted p-2 text-xs">{s.cloudformation}</pre></details>
            )}
            {isOwner ? (
              <form className="grid gap-2" onSubmit={(e) => {
                e.preventDefault();
                void run(() => api.enableFixes(connection.id, { role_arn: roleArn.trim(), days, max_actions_per_day: perDay, spend_limit_monthly: spend, auto_request: auto, confirm }), "Automatic fixes are on.");
              }}>
                <div className="grid gap-1"><Label htmlFor="fx-role">Fixes role ARN</Label>
                  <Input id="fx-role" required value={roleArn} onChange={(e) => setRoleArn(e.target.value)} placeholder="arn:aws:iam::123456789012:role/CostLensFixes" /></div>
                <div className="flex gap-2">
                  <div className="grid gap-1"><Label htmlFor="fx-days">For (days)</Label>
                    <Input id="fx-days" type="number" min={1} max={90} className="w-24" value={days} onChange={(e) => setDays(Number(e.target.value))} /></div>
                  <div className="grid gap-1"><Label htmlFor="fx-day">Fixes a day at most</Label>
                    <Input id="fx-day" type="number" min={1} max={500} className="w-24" value={perDay} onChange={(e) => setPerDay(Number(e.target.value))} /></div>
                  <div className="grid gap-1"><Label htmlFor="fx-spend">Purchases a month at most (USD)</Label>
                    <Input id="fx-spend" type="number" min={0} className="w-32" value={spend} onChange={(e) => setSpend(Number(e.target.value))} /></div>
                </div>
                <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
                  Auto clean-up: file requests for resources idle for a week (each still needs approval).</label>
                <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
                  I allow CostLens to change resources in this AWS account as described above.</label>
                <Button type="submit" disabled={!confirm}>Turn on automatic fixes</Button>
              </form>
            ) : (
              <p className="text-muted-foreground">Only an owner can turn automatic fixes on.</p>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
