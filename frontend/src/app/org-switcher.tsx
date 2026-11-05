import { Check, ChevronsUpDown, Compass, LogOut, Plus } from "lucide-react";
import { DropdownMenu as Menu } from "radix-ui";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { api, errorMessage } from "@/lib/api";
import { activeOrg, useMe, useSession } from "@/lib/session";

const item =
  "flex cursor-default select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none focus:bg-accent focus:text-accent-foreground [&_svg]:size-4";

export function OrgSwitcher() {
  const me = useMe();
  const { refresh } = useSession();
  const navigate = useNavigate();
  const current = activeOrg(me);

  async function switchTo(id: string) {
    try {
      await api.switchOrg(id);
      await refresh();
      navigate("/");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" className="max-w-56 justify-between" aria-label="Switch organisation">
          <span className="truncate">{current?.name ?? "Choose organisation"}</span>
          <ChevronsUpDown className="opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64">
        <Menu.Label className="px-2 py-1 text-xs text-muted-foreground">Organisations</Menu.Label>
        {me.organizations.map((org) => (
          <Menu.Item key={org.id} className={item} onSelect={() => void switchTo(org.id)}>
            <Check className={org.id === current?.id ? "" : "invisible"} />
            <span className="flex-1 truncate">{org.name}</span>
            {org.is_demo && <Badge variant="secondary">demo</Badge>}
          </Menu.Item>
        ))}
        <Menu.Separator className="my-1 h-px bg-border" />
        <Menu.Item className={item} onSelect={() => navigate("/welcome")}>
          <Plus /> New organisation
        </Menu.Item>
        {!me.organizations.some((o) => o.is_demo) && (
          <Menu.Item className={item} onSelect={() => navigate("/welcome")}>
            <Compass /> Explore the demo
          </Menu.Item>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function UserMenu() {
  const me = useMe();
  const { signOut } = useSession();
  const navigate = useNavigate();
  const initials = (me.user.name || me.user.email).slice(0, 2).toUpperCase();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Account menu" className="rounded-full">
          {me.user.avatar_url ? (
            <img src={me.user.avatar_url} alt="" className="size-8 rounded-full" referrerPolicy="no-referrer" />
          ) : (
            <span className="flex size-8 items-center justify-center rounded-full bg-accent text-xs font-semibold">
              {initials}
            </span>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <div className="px-2 py-1.5">
          <p className="truncate text-sm font-medium">{me.user.name}</p>
          <p className="truncate text-xs text-muted-foreground">{me.user.email}</p>
        </div>
        <Menu.Separator className="my-1 h-px bg-border" />
        <Menu.Item
          className={item}
          onSelect={() => void signOut().then(() => navigate("/sign-in", { replace: true }))}
        >
          <LogOut /> Sign out
        </Menu.Item>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
