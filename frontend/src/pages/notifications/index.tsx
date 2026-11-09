import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, type InboxItem, type NotificationPref } from "@/lib/api";
import { activeOrg, useMe } from "@/lib/session";
import { timeAgo } from "@/lib/time-ago";
import { useLoad } from "@/lib/use-load";
import { cn } from "@/lib/utils";

/** The notification centre (9.4): everything sent to you, and what you want sent. */
export function NotificationsPage() {
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Notifications</h1>
        <p className="text-sm text-muted-foreground">
          Alerts, finished and failed imports, approvals waiting for you and invitations, from all your organisations.
        </p>
      </div>
      <Inbox />
      <Preferences />
    </div>
  );
}

function Inbox() {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const { data, error, loading, setData } = useLoad(() => api.notifications({ unreadOnly }), [unreadOnly]);
  const [more, setMore] = useState(false);

  async function markRead(item: InboxItem) {
    try {
      await api.markNotificationRead(item.id);
      setData((page) =>
        page && {
          ...page,
          unread: Math.max(0, page.unread - 1),
          items: page.items.map((i) => (i.id === item.id ? { ...i, read_at: new Date().toISOString() } : i)),
        },
      );
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  async function readAll() {
    try {
      await api.markAllNotificationsRead();
      const now = new Date().toISOString();
      setData((page) => page && { ...page, unread: 0, items: page.items.map((i) => ({ ...i, read_at: i.read_at ?? now })) });
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  async function loadMore() {
    if (!data?.next_before) return;
    setMore(true);
    try {
      const next = await api.notifications({ unreadOnly, before: data.next_before });
      setData((page) => page && { ...next, items: [...page.items, ...next.items] });
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setMore(false);
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <div>
          <CardTitle>Inbox</CardTitle>
          <CardDescription>{data ? `${data.unread} unread` : " "}</CardDescription>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" aria-pressed={unreadOnly} onClick={() => setUnreadOnly((v) => !v)}>
            {unreadOnly ? "Show all" : "Unread only"}
          </Button>
          <Button variant="outline" size="sm" disabled={!data?.unread} onClick={() => void readAll()}>
            Mark all read
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {loading && !data && <p className="text-sm text-muted-foreground">Loading…</p>}
        {data && data.items.length === 0 && (
          <p className="text-sm text-muted-foreground">{unreadOnly ? "Nothing unread." : "No notifications yet."}</p>
        )}
        <ul className="divide-y">
          {data?.items.map((i) => (
            <li key={i.id} className="flex flex-col gap-1 py-3 sm:flex-row sm:items-start sm:gap-4">
              <div className="min-w-0 flex-1">
                <p className={cn("flex items-start gap-2 text-sm", !i.read_at && "font-medium")}>
                  {!i.read_at && <span aria-label="Unread" className="mt-1.5 size-2 shrink-0 rounded-full bg-primary" />}
                  <span>{i.title}</span>
                </p>
                {i.text && <p className="text-sm whitespace-pre-line text-muted-foreground">{i.text}</p>}
                <p className="text-xs text-muted-foreground">
                  {timeAgo(i.created_at)} · {i.organization}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                {i.link && (
                  <Button asChild variant="outline" size="sm">
                    <Link to={i.link} onClick={() => !i.read_at && void markRead(i)}>
                      Open
                    </Link>
                  </Button>
                )}
                {!i.read_at && (
                  <Button variant="ghost" size="sm" onClick={() => void markRead(i)} aria-label={`Mark read: ${i.title}`}>
                    Mark read
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
        {data?.next_before && (
          <Button variant="outline" size="sm" className="mt-3" disabled={more} onClick={() => void loadMore()}>
            Show older
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function Preferences() {
  const me = useMe();
  const org = activeOrg(me);
  const { data, error, setData } = useLoad(() => api.notificationPrefs(), [me.active_organization_id]);
  const [saving, setSaving] = useState(false);
  if (!org) return null;

  function toggle(event: string, field: "inbox" | "email") {
    setData((prefs) => prefs?.map((p) => (p.event === event ? { ...p, [field]: !p[field] } : p)) ?? null);
  }

  async function save(prefs: NotificationPref[]) {
    setSaving(true);
    try {
      setData(await api.saveNotificationPrefs(prefs));
      toast.success("Notification choices saved.");
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>What you receive in {org.name}</CardTitle>
        <CardDescription>
          Choose per kind whether it shows in your inbox and whether it is also emailed to you. Slack and Teams channels
          for the whole team are set by admins under Settings → Notifications.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {data && (
          <>
            <Table>
              <THead>
                <TR><TH>Kind</TH><TH className="w-20 text-center">Inbox</TH><TH className="w-20 text-center">Email</TH></TR>
              </THead>
              <TBody>
                {data.map((p) => (
                  <TR key={p.event}>
                    <TD>{p.label}</TD>
                    <TD className="text-center">
                      <input type="checkbox" className="size-4 accent-primary" aria-label={`${p.label} in inbox`} checked={p.inbox} onChange={() => toggle(p.event, "inbox")} />
                    </TD>
                    <TD className="text-center">
                      <input type="checkbox" className="size-4 accent-primary" aria-label={`${p.label} by email`} checked={p.email} onChange={() => toggle(p.event, "email")} />
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <Button className="mt-4" disabled={saving} onClick={() => void save(data)}>
              Save choices
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}
