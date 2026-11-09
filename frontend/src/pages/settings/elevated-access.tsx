import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage } from "@/lib/api";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const DURATIONS = [[30, "30 minutes"], [60, "1 hour"], [240, "4 hours"], [480, "8 hours"], [1440, "1 day"], [4320, "3 days"]] as const;

/** Ask for a higher role for a while; admins approve. Everything is in the audit log. */
export function ElevatedAccessCard() {
  const me = useMe();
  const isAdmin = atLeast(me.role, "admin");
  const demo = Boolean(activeOrg(me)?.is_demo);
  const list = useLoad(() => api.elevatedAccess(), [me.active_organization_id]);
  const [role, setRole] = useState("admin");
  const [minutes, setMinutes] = useState(60);
  const [reason, setReason] = useState("");

  async function act(fn: () => Promise<unknown>, ok?: string) {
    try {
      await fn();
      if (ok) toast.success(ok);
    } catch (error) {
      toast.error(errorMessage(error));
    }
    await list.reload();
  }

  async function ask(e: FormEvent) {
    e.preventDefault();
    await act(() => api.requestAccess({ role, minutes, reason: reason.trim() }), "Request sent to the admins.");
    setReason("");
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Temporary access</CardTitle>
        <CardDescription>Ask for a higher role for a limited time. Another admin approves it, it ends on its own, and every step is in the audit log.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        <ul className="grid gap-2" aria-label="Access requests">
          {(list.data ?? []).map((g) => (
            <li key={g.id} className="flex flex-wrap items-center gap-2 rounded-md border p-2">
              <span className="font-medium">{g.user}</span> → {g.role}
              <Badge variant={g.status === "active" ? "default" : "outline"}>{g.status}</Badge>
              <span className="text-muted-foreground">{g.reason}{g.expires_at && g.status === "active" ? ` · until ${new Date(g.expires_at).toLocaleString()}` : ""}</span>
              <span className="ml-auto flex gap-1">
                {isAdmin && g.status === "requested" && g.user_id !== me.user.id && (
                  <>
                    <Button size="sm" onClick={() => void act(() => api.decideAccess(g.id, "approve"), "Approved.")}>Approve</Button>
                    <Button size="sm" variant="ghost" onClick={() => void act(() => api.decideAccess(g.id, "deny"))}>Deny</Button>
                  </>
                )}
                {(g.status === "active" || g.status === "requested") && (isAdmin || g.user_id === me.user.id) && (
                  <Button size="sm" variant="ghost" onClick={() => void act(() => api.decideAccess(g.id, "revoke"))}>End</Button>
                )}
              </span>
            </li>
          ))}
          {list.data?.length === 0 && <li className="text-muted-foreground">No requests.</li>}
        </ul>
        {!demo && me.role !== "owner" && (
          <form onSubmit={(e) => void ask(e)} className="flex flex-wrap items-end gap-2">
            <div className="grid gap-1"><Label htmlFor="ea-role">Role</Label>
              <NativeSelect id="ea-role" value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="member">Member</option><option value="admin">Admin</option><option value="owner">Owner</option>
              </NativeSelect></div>
            <div className="grid gap-1"><Label htmlFor="ea-for">For</Label>
              <NativeSelect id="ea-for" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
                {DURATIONS.map(([m, l]) => <option key={m} value={m}>{l}</option>)}
              </NativeSelect></div>
            <div className="grid min-w-64 flex-1 gap-1"><Label htmlFor="ea-reason">Reason</Label>
              <Input id="ea-reason" required minLength={5} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} /></div>
            <Button type="submit">Ask for access</Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
