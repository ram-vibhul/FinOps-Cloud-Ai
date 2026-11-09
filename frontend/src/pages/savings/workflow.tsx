import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage, type Finding, type Member } from "@/lib/api";
import { money } from "@/lib/format";
import { useLoad } from "@/lib/use-load";
import { FIXABLE } from "./fixes";
import { FindingLinks } from "./links";

type Status = Finding["status"];
const NEXT: Record<Status, { to: Status; label: string; admin?: boolean }[]> = {
  open: [{ to: "approved", label: "Approve", admin: true }, { to: "applied", label: "Mark applied" }, { to: "dismissed", label: "Dismiss" }],
  approved: [{ to: "applied", label: "Mark applied" }, { to: "open", label: "Back to open" }, { to: "dismissed", label: "Dismiss" }],
  applied: [{ to: "verified", label: "Verify by hand", admin: true }, { to: "open", label: "Reopen" }],
  verified: [{ to: "open", label: "Reopen" }],
  dismissed: [{ to: "open", label: "Reopen" }],
  gone: [{ to: "open", label: "Reopen" }],
};
const EVENT: Record<string, string> = { status: "changed status", owner: "set the owner", comment: "commented", measured: "measured" };

const REASONS = {
  not_applicable: "Doesn't apply to us", accepted_risk: "We accept this cost", false_positive: "The finding is wrong",
  planned: "Already planned", other: "Other",
} as const;

function DismissForm({ f, isAdmin, onDone }: { f: Finding; isAdmin: boolean; onDone: (changed: boolean) => void }) {
  const [reason, setReason] = useState<keyof typeof REASONS>("not_applicable");
  const [scope, setScope] = useState("finding");
  const [tag, setTag] = useState("");
  const [note, setNote] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      const r = await api.dismissFinding(f.id, { reason, scope, note: note.trim(), tag: scope === "tag" ? tag.trim() : undefined });
      toast.success(r.dismissed > 1 ? `${r.dismissed} findings dismissed by this rule.` : "Dismissed.");
      onDone(true);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} aria-label="Dismiss" className="grid gap-2 rounded-md bg-muted/50 p-2 text-sm sm:grid-cols-2">
      <label className="grid gap-1">
        Why
        <NativeSelect aria-label="Why" value={reason} onChange={(e) => setReason(e.target.value as keyof typeof REASONS)}>
          {Object.entries(REASONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </NativeSelect>
      </label>
      <label className="grid gap-1">
        For
        <NativeSelect aria-label="For" value={scope} onChange={(e) => setScope(e.target.value)}>
          <option value="finding">Just this finding</option>
          {f.resource_id && <option value="resource">This resource, for every check</option>}
          {isAdmin && f.provider === "aws" && <option value="tag">Resources with a tag, for this check</option>}
          {isAdmin && f.account_id && <option value="account">This account, for this check</option>}
          {isAdmin && <option value="check">This check everywhere</option>}
        </NativeSelect>
      </label>
      {scope === "tag" && (
        <Input aria-label="Tag" placeholder="env=sandbox" value={tag} onChange={(e) => setTag(e.target.value)} required pattern="[^=]+=.+" />
      )}
      <Input aria-label="Note" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
      <div className="flex gap-2 sm:col-span-2">
        <Button type="submit" size="sm">Dismiss</Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => onDone(false)}>Cancel</Button>
      </div>
    </form>
  );
}

export function WorkflowPanel({ f, isAdmin, members, onChanged }: {
  f: Finding; isAdmin: boolean; members: Member[]; onChanged: () => void;
}) {
  const history = useLoad(() => api.findingHistory(f.id), [f.id, f.status, f.owner_id]);
  const [comment, setComment] = useState("");

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      onChanged();
      await history.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const [dismissing, setDismissing] = useState(false);

  async function move(to: Status) {
    if (to === "dismissed") {
      setDismissing(true);
      return;
    }
    await act(() => api.updateFinding(f.id, { status: to }));
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!comment.trim()) return;
    await act(() => api.commentFinding(f.id, comment.trim()));
    setComment("");
  }

  return (
    <div className="grid gap-3 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        {NEXT[f.status].filter((n) => !n.admin || isAdmin).map((n) => (
          <Button key={n.to} size="sm" variant={n.to === "approved" || n.to === "applied" ? "secondary" : "ghost"}
            onClick={() => void move(n.to)}>{n.label}</Button>
        ))}
        {f.status === "applied" && (
          <Button size="sm" variant="ghost" onClick={() => void act(() => api.measureFinding(f.id))}>Measure now</Button>
        )}
        <label className="ml-auto flex items-center gap-2 text-xs">
          Owner
          <NativeSelect aria-label="Owner" value={f.owner_id ?? ""}
            onChange={(e) => void act(() => api.setFindingOwner(f.id, e.target.value || null))}>
            <option value="">Nobody</option>
            {members.map((m) => <option key={m.user_id} value={m.user_id}>{m.name || m.email}</option>)}
          </NativeSelect>
        </label>
      </div>
      <FindingLinks findingId={f.id} onOpened={() => void history.reload()} />
      {FIXABLE.has(f.detector) && (f.status === "open" || f.status === "approved") && (
        <div>
          <Button size="sm" variant="outline" onClick={() => void act(async () => {
            await api.requestFix(f.id);
            toast.success("Fix requested. Another admin must approve it.");
          })}>
            Ask CostLens to fix this
          </Button>
        </div>
      )}
      {dismissing && (
        <DismissForm f={f} isAdmin={isAdmin} onDone={(changed) => { setDismissing(false); if (changed) onChanged(); }} />
      )}
      {(f.realised_monthly != null || f.measurement?.before_daily != null) && (
        <p className="text-sm">
          Realised: <span className="font-medium tabular-nums">{f.realised_monthly != null ? money(f.realised_monthly, f.currency) : "–"}</span>
          {" "}per month (expected {money(f.savings_monthly, f.currency)})
          {f.measurement?.days_after != null && <span className="text-muted-foreground"> · {f.measurement.days_after} day(s) measured since the change</span>}
        </p>
      )}
      <ol className="grid gap-1 text-xs">
        {(history.data ?? []).map((h) => (
          <li key={`${h.at}-${h.kind}`}>
            <span className="text-muted-foreground">{new Date(h.at).toLocaleString()} · {h.who} {EVENT[h.kind] ?? h.kind}</span>
            {h.kind === "status" && <span> {String(h.data.from)} → {String(h.data.to)}</span>}
            {h.text && <span>: {h.text}</span>}
          </li>
        ))}
      </ol>
      <form onSubmit={(e) => void send(e)} className="flex gap-2">
        <Input aria-label="Comment" value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Add a comment" maxLength={2000} />
        <Button type="submit" size="sm" variant="outline">Comment</Button>
      </form>
    </div>
  );
}
