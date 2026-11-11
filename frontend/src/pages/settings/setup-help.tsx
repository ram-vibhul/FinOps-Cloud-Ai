import { Copy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { api, type CloudConnection, type SetupHelp } from "@/lib/api";
import { useLoad } from "@/lib/use-load";

function copy(text: string) {
  void navigator.clipboard?.writeText(text).then(() => toast.success("Copied."), () => toast.error("Could not copy."));
}

/** Roles and copyable commands (no secrets in them). */
export function RolesAndCommands({ help }: { help: SetupHelp }) {
  const text = help.commands.join("\n");
  return (
    <div className="grid gap-2 text-sm">
      <ul className="grid gap-1" aria-label="Read-only roles">
        {help.roles.map((r) => (
          <li key={`${r.role}-${r.scope}`}><span className="font-medium">{r.role}</span> on <code className="break-all text-xs">{r.scope}</code>
            <span className="block text-xs text-muted-foreground">{r.why}</span></li>
        ))}
      </ul>
      <div className="relative">
        <pre aria-label="Commands" className="max-h-56 overflow-auto rounded-md bg-muted p-3 text-xs">{text}</pre>
        <Button type="button" size="sm" variant="ghost" className="absolute right-1 top-1" onClick={() => copy(text)}><Copy /> Copy</Button>
      </div>
    </div>
  );
}

/** Before connecting: which roles to grant (no scope known yet). */
export function RolesPanel({ provider }: { provider: "azure" | "gcp" }) {
  const [open, setOpen] = useState(false);
  const help = useLoad(() => (open ? api.setupHelp(provider) : Promise.resolve(null)), [open, provider]);
  return (
    <details className="text-sm" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer">Which roles to grant (commands)</summary>
      {help.data && <div className="mt-2"><RolesAndCommands help={help.data} /></div>}
    </details>
  );
}

/** For a connected Azure or Google Cloud account: roles, commands and what the last collection saw. */
export function SetupHelpDialog({ connection }: { connection: CloudConnection }) {
  const [open, setOpen] = useState(false);
  const help = useLoad(() => (open ? api.connectionSetupHelp(connection.id) : Promise.resolve(null)), [open, connection.id]);
  const seen = help.data?.last_collection;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button variant="ghost" size="sm">Setup and last collection</Button></DialogTrigger>
      <DialogContent title={`${connection.name}: setup and last collection`} description="The read-only roles CloudCostRadar needs, and what it saw the last time it collected.">
        {seen && (
          <section aria-label="Last collection" className="grid gap-1 text-sm">
            <p>Scope: <code className="break-all text-xs">{seen.scope ?? "—"}</code></p>
            <p>{seen.rows.toLocaleString()} cost row(s){seen.data_from ? `, ${seen.data_from} to ${seen.data_to}` : ""}.
              {seen.checked_at ? ` Last success ${new Date(seen.checked_at).toLocaleString()}.` : " No successful collection yet."}</p>
            {seen.error && <p className="text-destructive">Last error: {seen.error}</p>}
            {seen.runs.length > 0 && (
              <ul className="grid gap-0.5 text-xs text-muted-foreground">
                {seen.runs.map((r) => (
                  <li key={`${r.billing_period}-${r.finished_at}`}>{r.billing_period.slice(0, 7)}: {r.status}, {r.rows} row(s){r.error ? ` — ${r.error}` : ""}</li>
                ))}
              </ul>
            )}
            {seen.advice && <p className="text-xs">Recommendations: {seen.advice.count ?? 0} imported{seen.advice.errors && Object.keys(seen.advice.errors).length ? `, ${Object.keys(seen.advice.errors).length} place(s) with errors` : ""}.</p>}
          </section>
        )}
        {help.data && <RolesAndCommands help={help.data} />}
      </DialogContent>
    </Dialog>
  );
}
