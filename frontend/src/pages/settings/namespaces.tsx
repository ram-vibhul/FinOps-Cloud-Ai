import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, errorMessage, type K8sCluster } from "@/lib/api";

export function NamespacesPanel({ cluster, onSaved }: { cluster: K8sCluster; onSaved: () => void }) {
  const names = cluster.namespaces ?? [];
  const including = (cluster.include_namespaces ?? []).length > 0;
  const [mode, setMode] = useState<"exclude" | "include">(including ? "include" : "exclude");
  const [ticked, setTicked] = useState<Set<string>>(
    new Set(including ? cluster.include_namespaces : cluster.exclude_namespaces),
  );

  function toggle(name: string) {
    const next = new Set(ticked);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setTicked(next);
  }

  async function save() {
    const list = [...ticked].toSorted();
    if (mode === "include" && list.length === 0) {
      toast.error("Tick at least one namespace to watch.");
      return;
    }
    try {
      await api.updateCluster(cluster.id, mode === "include"
        ? { include_namespaces: list, exclude_namespaces: [] }
        : { include_namespaces: [], exclude_namespaces: list });
      toast.success("Saved. The agent applies this at its next check-in (within 5 minutes).");
      onSaved();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <fieldset className="basis-full grid gap-2 rounded-md border p-3 text-sm">
      <legend className="px-1 font-medium">Watched namespaces</legend>
      <div className="flex flex-wrap gap-4">
        <label className="flex items-center gap-2">
          <input type="radio" name={`mode-${cluster.id}`} checked={mode === "exclude"} onChange={() => setMode("exclude")} />
          All except the ticked ones
        </label>
        <label className="flex items-center gap-2">
          <input type="radio" name={`mode-${cluster.id}`} checked={mode === "include"} onChange={() => setMode("include")} />
          Only the ticked ones
        </label>
      </div>
      {names.length === 0 ? (
        <p className="text-muted-foreground">Namespaces appear after the agent's first report.</p>
      ) : (
        <div className="grid grid-cols-2 gap-1 sm:grid-cols-4">
          {names.map((n) => (
            <label key={n} className="flex items-center gap-2">
              <input type="checkbox" checked={ticked.has(n)} onChange={() => toggle(n)} /> {n}
            </label>
          ))}
        </div>
      )}
      <Button size="sm" className="justify-self-start" onClick={() => void save()}>Save namespaces</Button>
    </fieldset>
  );
}
