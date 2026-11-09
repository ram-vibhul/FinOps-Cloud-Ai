import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, type CloudConnection, errorMessage } from "@/lib/api";

type Rule = { match: string; app: string; team: string };

/** Bedrock spend per app and team, from model invocation logs in S3. */
export function BedrockLogsDialog({ connection, onSaved }: { connection: CloudConnection; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [bucket, setBucket] = useState(connection.bedrock_logs?.bucket ?? "");
  const [prefix, setPrefix] = useState(connection.bedrock_logs?.prefix ?? "");
  const [rules, setRules] = useState<Rule[]>([]);

  async function save(e: FormEvent) {
    e.preventDefault();
    try {
      await api.setBedrockLogs(connection.id, { bedrock_log_bucket: bucket.trim(), bedrock_log_prefix: prefix.trim(), bedrock_rules: rules.filter((r) => r.match.trim()) });
      if (bucket.trim()) {
        const out = await api.importBedrockLogs(connection.id);
        if (out.error) toast.error(`Saved, but reading the logs failed (${out.error}). Update the role with the new template.`);
        else toast.success(`Saved. Read ${out.events ?? 0} model call(s) from ${out.files ?? 0} file(s).`);
      } else {
        toast.success("Bedrock logs turned off.");
      }
      setOpen(false);
      onSaved();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const setRule = (i: number, k: keyof Rule, v: string) => setRules(rules.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm">Bedrock logs</Button>
      </DialogTrigger>
      <DialogContent title="Bedrock spend per app and team"
        description="Read Bedrock model invocation logs from S3 to split Bedrock spend by app and team. CostLens keeps only token counts and who called; turn off text delivery in the Bedrock logging settings so prompts are never written to the logs. After saving, update the read-only role with the new template (it adds read access to this bucket only).">
        <form onSubmit={(e) => void save(e)} className="grid gap-3">
          <div className="grid gap-1"><Label htmlFor="bl-bucket">Log bucket</Label>
            <Input id="bl-bucket" value={bucket} onChange={(e) => setBucket(e.target.value)} placeholder="acme-bedrock-logs (empty turns it off)" /></div>
          <div className="grid gap-1"><Label htmlFor="bl-prefix">Prefix (optional)</Label>
            <Input id="bl-prefix" value={prefix} onChange={(e) => setPrefix(e.target.value)} placeholder="bedrock" /></div>
          <fieldset className="grid gap-2 text-sm">
            <legend className="mb-1 font-medium">Callers without app or team in request metadata</legend>
            {rules.map((r, i) => (
              <div key={i} className="grid grid-cols-3 gap-2">
                <Input aria-label={`Role contains ${i + 1}`} placeholder="role name contains" value={r.match} onChange={(e) => setRule(i, "match", e.target.value)} />
                <Input aria-label={`App ${i + 1}`} placeholder="app" value={r.app} onChange={(e) => setRule(i, "app", e.target.value)} />
                <Input aria-label={`Team ${i + 1}`} placeholder="team" value={r.team} onChange={(e) => setRule(i, "team", e.target.value)} />
              </div>
            ))}
            <Button type="button" size="sm" variant="outline" className="justify-self-start" onClick={() => setRules([...rules, { match: "", app: "", team: "" }])}>Add a rule</Button>
          </fieldset>
          <Button type="submit">Save and read now</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
