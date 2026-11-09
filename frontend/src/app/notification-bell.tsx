import { Bell } from "lucide-react";
import { DropdownMenu as Menu } from "radix-ui";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { api, type InboxItem } from "@/lib/api";
import { timeAgo } from "@/lib/time-ago";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;
const row =
  "flex cursor-default select-none flex-col gap-0.5 rounded-sm px-2 py-1.5 text-sm outline-none focus:bg-accent focus:text-accent-foreground";

/** The unread badge and the latest notifications, in the header (9.4). */
export function NotificationBell() {
  const navigate = useNavigate();
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState<InboxItem[] | null>(null);

  const refreshCount = useCallback(async () => {
    try {
      setUnread((await api.unreadNotifications()).unread);
    } catch {
      // The badge is a convenience: a failed poll keeps the last count.
    }
  }, []);

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect -- polling the unread count
    void refreshCount();
    const timer = setInterval(() => void refreshCount(), POLL_MS);
    return () => clearInterval(timer);
  }, [refreshCount]);

  async function onOpenChange(isOpen: boolean) {
    if (!isOpen) return;
    try {
      const page = await api.notifications({ limit: 8 });
      setItems(page.items);
      setUnread(page.unread);
    } catch {
      setItems([]);
    }
  }

  async function open(item: InboxItem) {
    if (!item.read_at) {
      setUnread((n) => Math.max(0, n - 1));
      await api.markNotificationRead(item.id).catch(() => undefined);
    }
    navigate(item.link ?? "/notifications");
  }

  async function readAll() {
    setUnread(0);
    setItems((list) => list?.map((i) => ({ ...i, read_at: i.read_at ?? new Date().toISOString() })) ?? null);
    await api.markAllNotificationsRead().catch(() => undefined);
  }

  const label = unread ? `Notifications, ${unread} unread` : "Notifications";
  return (
    <DropdownMenu onOpenChange={(o) => void onOpenChange(o)}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={label} className="relative">
          <Bell />
          {unread > 0 && (
            <span
              aria-hidden="true"
              className="absolute -top-0.5 -right-0.5 flex min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] leading-4 font-semibold text-white"
            >
              {unread > 99 ? "99+" : unread}
            </span>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80 max-w-[calc(100vw-2rem)]">
        <div className="flex items-center justify-between px-2 py-1.5">
          <span className="text-sm font-medium">Notifications</span>
          {unread > 0 && (
            <button type="button" className="text-xs text-primary hover:underline" onClick={() => void readAll()}>
              Mark all read
            </button>
          )}
        </div>
        <Menu.Separator className="my-1 h-px bg-border" />
        {items === null ? (
          <p className="px-2 py-3 text-sm text-muted-foreground">Loading…</p>
        ) : items.length === 0 ? (
          <p className="px-2 py-3 text-sm text-muted-foreground">Nothing new. Alerts, finished imports and approvals waiting for you show here.</p>
        ) : (
          items.map((i) => (
            <Menu.Item key={i.id} className={row} onSelect={() => void open(i)}>
              <span className={cn("flex items-start gap-2", !i.read_at && "font-medium")}>
                {!i.read_at && <span aria-label="Unread" className="mt-1.5 size-2 shrink-0 rounded-full bg-primary" />}
                <span className="line-clamp-2">{i.title}</span>
              </span>
              <span className="text-xs text-muted-foreground">
                {timeAgo(i.created_at)} · {i.organization}
              </span>
            </Menu.Item>
          ))
        )}
        <Menu.Separator className="my-1 h-px bg-border" />
        <Menu.Item className={cn(row, "text-center")} onSelect={() => navigate("/notifications")}>
          All notifications and settings
        </Menu.Item>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
