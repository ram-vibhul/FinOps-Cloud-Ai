import { Copy, GitBranch } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorMessage, type InboundWebhook } from "@/lib/api";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

function copy(text: string) {
  void navigator.clipboard?.writeText(text).then(() => toast.success("Copied."));
}

export function GithubWebhooks() {
  const me = useMe();
  const canManage = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const list = useLoad(() => (canManage ? api.webhooks() : Promise.resolve([] as InboundWebhook[])), [me.active_organization_id, canManage]);
  const [created, setCreated] = useState<InboundWebhook | null>(null);
  if (!canManage) return null;

  async function create() {
    try {
      setCreated(await api.createGithubWebhook("GitHub"));
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function remove(id: string) {
    try {
      await api.deleteWebhook(id);
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-medium">Deployments from GitHub</h3>
          <p className="text-sm text-muted-foreground">
            Show deployments next to costs and Kubernetes incidents. In GitHub: Settings → Webhooks → Add webhook, content
            type application/json, events "Deployment statuses", "Releases" and "Workflow runs" (GitHub Actions workflows
            whose name mentions deploy, release, rollout, CD, production and similar).
          </p>
        </div>
        <Button variant="outline" onClick={() => void create()}><GitBranch /> Add GitHub webhook</Button>
      </div>
      {created?.secret && (
        <output className="grid gap-2 rounded-lg border p-3 text-sm">
          <p className="font-medium">Copy these into GitHub now — the secret is not shown again.</p>
          <Label htmlFor="hook-url">Payload URL</Label>
          <div className="flex gap-2">
            <Input id="hook-url" readOnly value={created.url} />
            <Button variant="outline" size="icon" aria-label="Copy URL" onClick={() => copy(created.url)}><Copy /></Button>
          </div>
          <Label htmlFor="hook-secret">Secret</Label>
          <div className="flex gap-2">
            <Input id="hook-secret" readOnly value={created.secret} />
            <Button variant="outline" size="icon" aria-label="Copy secret" onClick={() => copy(created.secret ?? "")}><Copy /></Button>
          </div>
        </output>
      )}
      <ul className="flex flex-col gap-1 text-sm">
        {(list.data ?? []).filter((h) => h.kind === "github").map((h) => (
          <li key={h.id} className="flex flex-wrap items-center justify-between gap-2">
            <span className="truncate">{h.name} — {h.last_received_at ? `last event ${new Date(h.last_received_at).toLocaleString()}` : "no events yet"}</span>
            <Button variant="ghost" size="sm" onClick={() => void remove(h.id)}>Remove</Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
