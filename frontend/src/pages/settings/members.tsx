import { Copy, UserPlus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, type Role } from "@/lib/api";
import { activeOrg, atLeast, useMe, useSession } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const ROLES: { value: Role; label: string; help: string }[] = [
  { value: "viewer", label: "Viewer", help: "Can see everything, change nothing." },
  { value: "member", label: "Member", help: "Can act on findings, budgets and reports." },
  { value: "admin", label: "Admin", help: "Can manage connections, members and settings." },
  { value: "owner", label: "Owner", help: "Full control, including billing." },
];

export function MembersTab() {
  const me = useMe();
  const { refresh } = useSession();
  const canManage = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const members = useLoad(() => api.members(), [me.active_organization_id]);
  const invites = useLoad(() => (canManage ? api.invitations() : Promise.resolve([])), [me.active_organization_id, canManage]);

  async function act(action: () => Promise<unknown>, done: string) {
    try {
      await action();
      toast.success(done);
      await Promise.all([members.reload(), invites.reload()]);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function leave() {
    if (!window.confirm("Leave this organisation? You will need a new invitation to come back.")) return;
    await act(() => api.removeMember(me.user.id), "You left the organisation.");
    await refresh();
  }

  const assignable = ROLES.filter((r) => r.value !== "owner" || me.role === "owner");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">Members</h2>
        {canManage && <InviteDialog roles={assignable} onInvited={() => void invites.reload()} />}
      </div>
      {members.error && <p role="alert" className="text-sm text-destructive">{members.error}</p>}
      <Table>
        <THead>
          <TR>
            <TH>Person</TH>
            <TH>Role</TH>
            <TH className="text-right">
              <span className="sr-only">Actions</span>
            </TH>
          </TR>
        </THead>
        <TBody>
          {(members.data ?? []).map((m) => {
            const self = m.user_id === me.user.id;
            const editable = canManage && !self && (m.role !== "owner" || me.role === "owner");
            return (
              <TR key={m.user_id}>
                <TD>
                  <div className="font-medium">{m.name || m.email}</div>
                  <div className="text-xs text-muted-foreground">{m.email}</div>
                </TD>
                <TD>
                  {editable ? (
                    <NativeSelect
                      aria-label={`Role for ${m.email}`}
                      value={m.role}
                      onChange={(e) => void act(() => api.changeRole(m.user_id, e.target.value as Role), "Role updated.")}
                    >
                      {assignable.map((r) => (
                        <option key={r.value} value={r.value}>
                          {r.label}
                        </option>
                      ))}
                    </NativeSelect>
                  ) : (
                    <Badge variant="secondary">{m.role}</Badge>
                  )}
                </TD>
                <TD className="text-right">
                  {self ? (
                    <Button variant="ghost" size="sm" onClick={() => void leave()}>
                      Leave
                    </Button>
                  ) : editable ? (
                    <div className="flex justify-end gap-1">
                      {me.role === "owner" && (
                        <Button variant="ghost" size="sm"
                          onClick={() => window.confirm(`Make ${m.email} the owner? You will become an admin.`) &&
                            void act(() => api.transferOwnership(m.user_id), "Ownership transferred.").then(refresh)}>
                          Make owner
                        </Button>
                      )}
                      <Button variant="ghost" size="sm"
                        onClick={() => window.confirm(`Remove ${m.email}?`) &&
                          void act(() => api.removeMember(m.user_id), "Member removed.")}>
                        Remove
                      </Button>
                    </div>
                  ) : null}
                </TD>
              </TR>
            );
          })}
        </TBody>
      </Table>
      {canManage && (invites.data?.length ?? 0) > 0 && (
        <section className="flex flex-col gap-2">
          <h3 className="font-medium">Pending invitations</h3>
          <Table>
            <TBody>
              {(invites.data ?? []).map((inv) => (
                <TR key={inv.id}>
                  <TD>{inv.email}</TD>
                  <TD><Badge variant="outline">{inv.role}</Badge></TD>
                  <TD className="text-xs text-muted-foreground">
                    expires {new Date(inv.expires_at).toLocaleDateString()}
                  </TD>
                  <TD className="text-right">
                    <Button variant="ghost" size="sm"
                      onClick={() => void act(() => api.revokeInvitation(inv.id), "Invitation revoked.")}>
                      Revoke
                    </Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </section>
      )}
    </div>
  );
}

function InviteDialog({ roles, onInvited }: { roles: typeof ROLES; onInvited: () => void }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [link, setLink] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      const created = await api.invite(email.trim(), role);
      setLink(created.invite_url);
      toast.success(`Invitation sent to ${created.email}.`);
      onInvited();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) { setLink(null); setEmail(""); } }}>
      <DialogTrigger asChild>
        <Button><UserPlus /> Invite people</Button>
      </DialogTrigger>
      <DialogContent title="Invite someone" description="They get an email with a link that works only for that address.">
        {link ? (
          <div className="grid gap-2">
            <Label htmlFor="invite-link">Invitation link (also emailed)</Label>
            <div className="flex gap-2">
              <Input id="invite-link" readOnly value={link} />
              <Button variant="outline" size="icon" aria-label="Copy link"
                onClick={() => void navigator.clipboard?.writeText(link).then(() => toast.success("Copied."))}>
                <Copy />
              </Button>
            </div>
          </div>
        ) : (
          <form onSubmit={(e) => void submit(e)} className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="invite-email">Email</Label>
              <Input id="invite-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="invite-role">Role</Label>
              <NativeSelect id="invite-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
                {roles.map((r) => (
                  <option key={r.value} value={r.value}>{r.label} — {r.help}</option>
                ))}
              </NativeSelect>
            </div>
            <Button type="submit">Send invitation</Button>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
