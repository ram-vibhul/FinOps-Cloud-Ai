import { Tags } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, type DataQuality, errorMessage, type TagRule } from "@/lib/api";
import { money } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

function CoverageBar({ pct }: { pct: number }) {
  return (
    <div className="h-2 w-full rounded-full bg-muted" aria-hidden>
      <div className="h-2 rounded-full bg-primary" style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
    </div>
  );
}

export function DataQualityPage() {
  const me = useMe();
  const canEdit = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const report = useLoad<DataQuality>(() => api.dataQuality(30), [me.active_organization_id]);
  const rules = useLoad(() => api.tagRules(), [me.active_organization_id]);
  const r = report.data;

  async function addRule(rule: TagRule) {
    try {
      await api.saveTagRules([...(rules.data?.rules ?? []), rule]);
      await api.applyTagRules();
      toast.success("Merged. Existing data is being updated; new imports use the rule too.");
      await Promise.all([rules.reload(), report.reload()]);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Data quality</h1>
        <p className="text-sm text-muted-foreground">
          How well your costs are tagged, last 30 days. Tags decide which team or project pays.{" "}
          <Link to="/costs" className="underline underline-offset-4">Back to costs</Link>
        </p>
      </div>
      {r && r.taggable_cost === 0 ? (
        <EmptyState icon={Tags} title="No cost data to check yet">Connect a cloud account or upload a billing file first.</EmptyState>
      ) : r && (
        <>
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
            <Card className="gap-1 py-4">
              <CardContent className="flex flex-col gap-1 px-4">
                <span className="text-sm text-muted-foreground">Unallocated spend (no {r.unallocated.tag})</span>
                <span className="text-2xl font-semibold tabular-nums">{money(r.unallocated.cost, r.currency)}</span>
                <span className="text-xs text-muted-foreground">{r.unallocated.pct}% of taggable spend</span>
              </CardContent>
            </Card>
            {r.coverage.map((c) => (
              <Card key={c.key} className="gap-1 py-4">
                <CardContent className="flex flex-col gap-2 px-4">
                  <span className="text-sm text-muted-foreground">Tagged with “{c.key}”</span>
                  <span className="text-2xl font-semibold tabular-nums">{c.coverage_pct}%</span>
                  <CoverageBar pct={c.coverage_pct} />
                  <span className="text-xs text-muted-foreground">{money(c.untagged_cost, r.currency)} untagged</span>
                </CardContent>
              </Card>
            ))}
          </div>

          {(r.inconsistent_keys.length > 0 || r.inconsistent_values.length > 0) && (
            <Card>
              <CardHeader>
                <CardTitle>Spelling variants</CardTitle>
                <CardDescription>The same tag written differently splits your reports. Merge each into one spelling.</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                {r.inconsistent_keys.map((k) => (
                  <div key={k.canonical} className="flex flex-wrap items-center justify-between gap-2 border-b pb-2">
                    <span className="text-sm">
                      Tag keys {k.variants.map((v) => `“${v.key}” (${money(v.cost, r.currency)})`).join(", ")}
                    </span>
                    {canEdit && (
                      <Button size="sm" variant="outline" onClick={() => void addRule({
                        kind: "key", from: k.variants.map((v) => v.key).filter((x) => x !== k.suggested), to: k.suggested })}>
                        Merge into “{k.suggested}”
                      </Button>
                    )}
                  </div>
                ))}
                {r.inconsistent_values.map((v) => (
                  <div key={`${v.key}:${v.canonical}`} className="flex flex-wrap items-center justify-between gap-2 border-b pb-2">
                    <span className="text-sm">
                      {v.key}: {v.variants.map((x) => `“${x.value}”`).join(", ")}
                    </span>
                    {canEdit && (
                      <Button size="sm" variant="outline" onClick={() => void addRule({
                        kind: "value", key: v.key, from: v.variants.map((x) => x.value).filter((x) => x !== v.suggested), to: v.suggested })}>
                        Merge into “{v.suggested.trim()}”
                      </Button>
                    )}
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          <div className="grid gap-6 xl:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Biggest untagged resources</CardTitle>
                <CardDescription>Tag these first: they hold the most unallocated spend.</CardDescription>
              </CardHeader>
              <CardContent>
                <Table>
                  <THead><TR><TH>Resource</TH><TH>Service</TH><TH className="text-right">Cost</TH></TR></THead>
                  <TBody>
                    {r.top_unallocated_resources.map((x) => (
                      <TR key={x.resource_id}>
                        <TD className="max-w-56 truncate font-mono text-xs" title={x.resource_id}>{x.resource_id}</TD>
                        <TD>{x.service}</TD>
                        <TD className="text-right tabular-nums">{money(x.cost, r.currency)}</TD>
                      </TR>
                    ))}
                    {r.top_unallocated_resources.length === 0 && (
                      <TR><TD colSpan={3} className="py-6 text-center text-muted-foreground">Every resource has a {r.unallocated.tag} tag.</TD></TR>
                    )}
                  </TBody>
                </Table>
              </CardContent>
            </Card>
            <RequiredTags initial={r.required_tags} canEdit={canEdit} onSaved={() => void report.reload()} />
          </div>
        </>
      )}
    </div>
  );
}

function RequiredTags({ initial, canEdit, onSaved }: { initial: string[]; canEdit: boolean; onSaved: () => void }) {
  const [text, setText] = useState(initial.join(", "));
  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      await api.saveQualitySettings(text.split(",").map((t) => t.trim()).filter(Boolean));
      toast.success("Required tags saved.");
      onSaved();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Required tags</CardTitle>
        <CardDescription>Tags every resource should have. Coverage is measured against these.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={(e) => void save(e)} className="grid gap-2">
          <Label htmlFor="required-tags">Tag keys, separated by commas</Label>
          <Input id="required-tags" value={text} onChange={(e) => setText(e.target.value)} disabled={!canEdit} />
          {canEdit && <Button type="submit" className="justify-self-start">Save</Button>}
        </form>
      </CardContent>
    </Card>
  );
}
