import { Download } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, type AuditEvent, errorMessage } from "@/lib/api";

// Bookkeeping fields every row has; they tell a reader nothing (9.3 polish).
const HIDDEN_FIELDS = new Set(["id", "org_id", "created_at", "updated_at"]);
const UUID = /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i;

function show(value: unknown): string {
  if (value === null || value === undefined) return "—";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return UUID.test(text) ? `${text.slice(0, 8)}…` : text;
}

function describeChanges(changes: AuditEvent["changes"]): string {
  return Object.entries(changes)
    .filter(([field]) => !HIDDEN_FIELDS.has(field))
    .map(([field, [before, after]]) => {
      const label = field.replaceAll("_", " ");
      return before === null || before === undefined
        ? `${label}: ${show(after)}`
        : `${label}: ${show(before)} → ${show(after)}`;
    })
    .join("; ");
}

export function AuditTab() {
  const [filters, setFilters] = useState({ action: "", actor: "" });
  const [applied, setApplied] = useState(filters);
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (next?: string) => {
    try {
      const page = await api.auditEvents({ ...applied, cursor: next });
      setItems((prev) => (next ? [...prev, ...page.items] : page.items));
      setCursor(page.next_cursor);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [applied]);

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect -- loading data from the API
    void load();
  }, [load]);

  function apply(event: FormEvent) {
    event.preventDefault();
    setApplied({ ...filters });
  }

  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={apply} className="flex flex-wrap items-end gap-3">
        <div className="grid gap-1">
          <Label htmlFor="audit-action">Action</Label>
          <Input id="audit-action" placeholder="e.g. membership" value={filters.action}
            onChange={(e) => setFilters({ ...filters, action: e.target.value })} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="audit-actor">Who</Label>
          <Input id="audit-actor" placeholder="email" value={filters.actor}
            onChange={(e) => setFilters({ ...filters, actor: e.target.value })} />
        </div>
        <Button type="submit" variant="secondary">Filter</Button>
        <Button asChild variant="outline" className="ml-auto">
          <a href={api.auditExportUrl(applied)}><Download /> Export CSV</a>
        </Button>
      </form>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <Table>
        <THead>
          <TR><TH>When</TH><TH>Who</TH><TH>What</TH><TH>Details</TH></TR>
        </THead>
        <TBody>
          {items.map((e) => (
            <TR key={e.id}>
              <TD className="whitespace-nowrap text-xs">{new Date(e.occurred_at).toLocaleString()}</TD>
              <TD className="text-xs">{e.actor_label}</TD>
              <TD>
                <div>{e.summary}</div>
                <div className="font-mono text-xs text-muted-foreground">{e.action}</div>
              </TD>
              <TD className="max-w-sm text-xs text-muted-foreground">{describeChanges(e.changes)}</TD>
            </TR>
          ))}
          {items.length === 0 && (
            <TR><TD colSpan={4} className="py-8 text-center text-muted-foreground">No matching events.</TD></TR>
          )}
        </TBody>
      </Table>
      {cursor && (
        <Button variant="outline" className="self-center" onClick={() => void load(cursor)}>Load more</Button>
      )}
    </div>
  );
}
