import { useSearchParams } from "react-router";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { atLeast, useMe } from "@/lib/session";
import { AccountTab } from "./account";
import { AlertSettingsCard } from "./alerts";
import { AuditTab } from "./audit";
import { CloudsTab } from "./clouds";
import { ClustersTab } from "./clusters";
import { OwnerZone } from "./danger";
import { RetentionCard } from "./retention";
import { FxOverridesCard } from "./fx";
import { GeneralTab } from "./general";
import { MembersTab } from "./members";
import { SsoTab } from "./sso";

export function SettingsPage() {
  const me = useMe();
  const isAdmin = atLeast(me.role, "admin");
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "general";
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">Organisation, members, sign-in and the audit log.</p>
      </div>
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: v }, { replace: true })}>
        <TabsList>
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="clouds">Cloud accounts</TabsTrigger>
          <TabsTrigger value="kubernetes">Kubernetes</TabsTrigger>
          <TabsTrigger value="members">Members</TabsTrigger>
          {isAdmin && <TabsTrigger value="sso">Single sign-on</TabsTrigger>}
          {isAdmin && <TabsTrigger value="audit">Audit log</TabsTrigger>}
          <TabsTrigger value="account">Your account</TabsTrigger>
        </TabsList>
        <TabsContent value="general" className="mt-4 flex flex-col gap-4">
          <GeneralTab />
          <FxOverridesCard />
          <AlertSettingsCard />
          <RetentionCard />
          {me.role === "owner" && <OwnerZone />}
        </TabsContent>
        <TabsContent value="account" className="mt-4">
          <AccountTab />
        </TabsContent>
        <TabsContent value="clouds" className="mt-4">
          <CloudsTab />
        </TabsContent>
        <TabsContent value="kubernetes" className="mt-4">
          <ClustersTab />
        </TabsContent>
        <TabsContent value="members" className="mt-4">
          <MembersTab />
        </TabsContent>
        {isAdmin && (
          <TabsContent value="sso" className="mt-4">
            <SsoTab />
          </TabsContent>
        )}
        {isAdmin && (
          <TabsContent value="audit" className="mt-4">
            <AuditTab />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
