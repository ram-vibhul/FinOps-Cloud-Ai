import { ExternalLink } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage, type WorkIntegration } from "@/lib/api";
import { useLoad } from "@/lib/use-load";

const WHAT: Record<WorkIntegration["kind"], string> = { jira: "Jira ticket", servicenow: "ServiceNow ticket", github: "GitHub pull request", gitlab: "GitLab merge request" };

/** Tickets and pull requests for one finding, and a way to open a new one. */
export function FindingLinks({ findingId, onOpened }: { findingId: string; onOpened: () => void }) {
  const integrations = useLoad(() => api.workIntegrations(), []);
  const links = useLoad(() => api.findingLinks(findingId), [findingId]);
  const enabled = (integrations.data ?? []).filter((i) => i.enabled);
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const chosen = pick || enabled[0]?.id || "";

  async function open() {
    setBusy(true);
    try {
      const link = await api.openFindingLink(findingId, chosen);
      toast.success(`Opened ${link.external_id}.`);
      await links.reload();
      onOpened();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  if (!enabled.length && !(links.data ?? []).length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      {(links.data ?? []).map((l) => (
        <a key={l.id} href={l.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 underline underline-offset-4">
          {l.kind === "ticket" ? "Ticket" : "Pull request"} {l.external_id} <ExternalLink className="size-3" aria-hidden />
        </a>
      ))}
      {enabled.length > 0 && (
        <span className="ml-auto flex items-center gap-2">
          <NativeSelect aria-label="Open in" value={chosen} onChange={(e) => setPick(e.target.value)}>
            {enabled.map((i) => <option key={i.id} value={i.id}>{i.name} ({WHAT[i.kind]})</option>)}
          </NativeSelect>
          <Button size="sm" variant="outline" disabled={busy || !chosen} onClick={() => void open()}>
            {busy ? "Opening…" : "Open ticket / PR"}
          </Button>
        </span>
      )}
    </div>
  );
}
