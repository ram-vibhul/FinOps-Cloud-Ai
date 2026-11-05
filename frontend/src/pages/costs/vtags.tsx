import { Plus, Trash2, Wand2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage, type VirtualTag, type VirtualTagDef, type VTagMatch, type VTagPreview } from "@/lib/api";
import { money } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

type Field = "account" | "service" | "region" | "resource_contains" | "usage_type_contains" | "tag";
const FIELDS: { value: Field; label: string }[] = [
  { value: "account", label: "Account is" },
  { value: "service", label: "Service is" },
  { value: "region", label: "Region is" },
  { value: "tag", label: "Tag key=value" },
  { value: "resource_contains", label: "Resource id or name contains" },
  { value: "usage_type_contains", label: "Usage type contains" },
];

interface Condition { field: Field; values: string }
interface RuleDraft { value: string; conditions: Condition[] }

function toDraft(def?: VirtualTagDef): RuleDraft[] {
  return (def?.rules ?? []).map((r) => ({
    value: r.value,
    conditions: Object.entries(r.match).flatMap(([field, v]) =>
      field === "tag"
        ? Object.entries(v as Record<string, string[]>).map(([k, vals]) => ({ field: "tag" as Field, values: vals.map((x) => `${k}=${x}`).join(", ") }))
        : [{ field: field as Field, values: (v as string[]).join(", ") }],
    ),
  }));
}

function toMatch(conditions: Condition[]): VTagMatch {
  const match: VTagMatch = {};
  for (const c of conditions) {
    const parts = c.values.split(",").map((x) => x.trim()).filter(Boolean);
    if (!parts.length) continue;
    if (c.field === "tag") {
      match.tag = match.tag ?? {};
      for (const p of parts) {
        const [k, ...rest] = p.split("=");
        if (k && rest.length) (match.tag[k.trim()] = match.tag[k.trim()] ?? []).push(rest.join("=").trim());
      }
    } else {
      match[c.field] = [...(match[c.field] ?? []), ...parts];
    }
  }
  return match;
}

export function VirtualTagsPage() {
  const me = useMe();
  const canEdit = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const list = useLoad(() => api.virtualTags(), [me.active_organization_id]);
  const [editing, setEditing] = useState<VirtualTag | "new" | null>(null);

  async function remove(t: VirtualTag) {
    if (!window.confirm(`Delete virtual tag "${t.key}"?`)) return;
    try {
      await api.deleteVirtualTag(t.id);
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Virtual tags</h1>
          <p className="text-sm text-muted-foreground">
            Rules that assign a tag (for example a team) to spend that is untagged or tagged wrongly. Your cloud tags are not changed.{" "}
            <Link to="/costs" className="underline underline-offset-4">Back to costs</Link>
          </p>
        </div>
        {canEdit && <Button onClick={() => setEditing("new")}><Plus /> New virtual tag</Button>}
      </div>
      {(list.data ?? []).length === 0 && !list.loading ? (
        <EmptyState icon={Wand2} title="No virtual tags yet">
          Create one to give every cost a team, project or environment, even where tags are missing.
        </EmptyState>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {(list.data ?? []).map((t) => (
            <Card key={t.id}>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">vtag:{t.key} <Badge variant="secondary">{t.rules.length} rules</Badge></CardTitle>
                <CardDescription>{t.description || `Default: ${t.default_value ?? "none"}`}</CardDescription>
              </CardHeader>
              <CardContent className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => setEditing(t)} disabled={!canEdit}>Edit</Button>
                {canEdit && <Button variant="ghost" size="sm" onClick={() => void remove(t)}><Trash2 /> Delete</Button>}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      {editing && (
        <Editor initial={editing === "new" ? undefined : editing} onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); void list.reload(); }} />
      )}
    </div>
  );
}

function Editor({ initial, onClose, onSaved }: { initial?: VirtualTag; onClose: () => void; onSaved: () => void }) {
  const [key, setKey] = useState(initial?.key ?? "team");
  const [fallback, setFallback] = useState(initial?.default_value ?? "unallocated");
  const [override, setOverride] = useState(initial?.override_existing ?? false);
  const [rules, setRules] = useState<RuleDraft[]>(toDraft(initial).length ? toDraft(initial) : [{ value: "", conditions: [{ field: "account", values: "" }] }]);
  const [preview, setPreview] = useState<VTagPreview | null>(null);

  const definition = (): VirtualTagDef => ({
    key: key.trim(), default_value: fallback.trim() || null, override_existing: override,
    rules: rules.filter((r) => r.value.trim()).map((r) => ({ value: r.value.trim(), match: toMatch(r.conditions) })),
  });
  const update = (i: number, change: Partial<RuleDraft>) => setRules(rules.map((r, j) => (j === i ? { ...r, ...change } : r)));

  async function run(action: "preview" | "save") {
    try {
      if (action === "preview") setPreview(await api.previewVirtualTag(definition()));
      else {
        await api.saveVirtualTag(definition(), initial?.id);
        toast.success("Saved. Existing costs are being re-tagged.");
        onSaved();
      }
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title={initial ? `Edit vtag:${initial.key}` : "New virtual tag"} description="Rules are checked top to bottom; the first match wins." className="max-h-[90dvh] max-w-3xl overflow-y-auto">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="grid gap-1">
            <Label htmlFor="vt-key">Tag key</Label>
            <Input id="vt-key" value={key} onChange={(e) => setKey(e.target.value)} />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="vt-default">Value when no rule matches</Label>
            <Input id="vt-default" value={fallback} onChange={(e) => setFallback(e.target.value)} />
          </div>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} />
          Rules win over a real tag with the same key
        </label>
        <ol className="grid gap-3">
          {rules.map((rule, i) => (
            <li key={i} className="grid gap-2 rounded-lg border p-3">
              <div className="flex items-end gap-2">
                <div className="grid flex-1 gap-1">
                  <Label htmlFor={`vt-value-${i}`}>Rule {i + 1}: set value</Label>
                  <Input id={`vt-value-${i}`} value={rule.value} onChange={(e) => update(i, { value: e.target.value })} />
                </div>
                <Button variant="ghost" size="icon" aria-label={`Remove rule ${i + 1}`} onClick={() => setRules(rules.filter((_, j) => j !== i))}><Trash2 /></Button>
              </div>
              {rule.conditions.map((c, k) => (
                <div key={k} className="flex flex-wrap items-center gap-2">
                  <NativeSelect aria-label={`Rule ${i + 1} condition ${k + 1}`} value={c.field}
                    onChange={(e) => update(i, { conditions: rule.conditions.map((x, m) => (m === k ? { ...x, field: e.target.value as Field } : x)) })}>
                    {FIELDS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
                  </NativeSelect>
                  <Input className="flex-1" aria-label={`Rule ${i + 1} condition ${k + 1} values`} placeholder="values, separated by commas" value={c.values}
                    onChange={(e) => update(i, { conditions: rule.conditions.map((x, m) => (m === k ? { ...x, values: e.target.value } : x)) })} />
                </div>
              ))}
              <Button variant="ghost" size="sm" className="justify-self-start"
                onClick={() => update(i, { conditions: [...rule.conditions, { field: "service", values: "" }] })}>
                <Plus /> And another condition
              </Button>
            </li>
          ))}
        </ol>
        <Button variant="outline" className="justify-self-start" onClick={() => setRules([...rules, { value: "", conditions: [{ field: "account", values: "" }] }])}>
          <Plus /> Add rule
        </Button>
        {preview && (
          <div className="rounded-lg border p-3 text-sm" aria-live="polite">
            <p className="mb-2 font-medium">Last 30 days would be split like this:</p>
            <ul className="grid gap-1">
              {preview.values.map((v) => (
                <li key={v.value} className="flex justify-between"><span>{v.value}</span><span className="tabular-nums">{money(v.cost, "USD")}</span></li>
              ))}
            </ul>
          </div>
        )}
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => void run("preview")}>Preview</Button>
          <Button onClick={() => void run("save")}>Save</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
