import { useSearchParams } from "react-router";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { activeOrg, atLeast, useMe } from "@/lib/session";
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
import { NotificationsTab } from "./notifications";
import { ApiKeysTab } from "./api-keys";
import { CustomRolesCard } from "./custom-roles";
import { ElevatedAccessCard } from "./elevated-access";
import { PartnerApplyCard, PartnerLinkCard, PartnerTab } from "./partner";
import { DomainsCard, TeamsCard } from "./teams";
import { OutgoingWebhooksCard } from "./webhooks-out";
import { WorkIntegrationsTab } from "./work";
import { OwnershipTab } from "./ownership";
import { PlanTab } from "./plan";
import { ReliabilityTab } from "./reliability";
import { ScimCard } from "./scim";
import { SsoTab } from "./sso";

export function SettingsPage() {
  const me = useMe();
  const isAdmin = atLeast(me.role, "admin");
  const isPartner = Boolean(activeOrg(me)?.is_partner);
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
          <TabsTrigger value="reliability">Reliability</TabsTrigger>
          <TabsTrigger value="ownership">Ownership</TabsTrigger>
          <TabsTrigger value="members">Members</TabsTrigger>
          <TabsTrigger value="plan">Plan</TabsTrigger>
          {isAdmin && <TabsTrigger value="notifications">Notifications</TabsTrigger>}
          {isAdmin && <TabsTrigger value="integrations">Tickets &amp; PRs</TabsTrigger>}
          {isAdmin && <TabsTrigger value="api-keys">API keys</TabsTrigger>}
          {isAdmin && isPartner && <TabsTrigger value="partner">Partner</TabsTrigger>}
          {isAdmin && <TabsTrigger value="sso">Single sign-on</TabsTrigger>}
          {isAdmin && <TabsTrigger value="audit">Audit log</TabsTrigger>}
          <TabsTrigger value="account">Your account</TabsTrigger>
        </TabsList>
        <TabsContent value="general" className="mt-4 flex flex-col gap-4">
          <GeneralTab />
          <FxOverridesCard />
          <AlertSettingsCard />
          <RetentionCard />
          <PartnerLinkCard />
          <PartnerApplyCard />
          {me.role === "owner" && <OwnerZone />}
        </TabsContent>
        <TabsContent value="plan" className="mt-4">
          <PlanTab />
        </TabsContent>
        <TabsContent value="account" className="mt-4">
          <AccountTab />
        </TabsContent>
        <TabsContent value="clouds" className="mt-4">
          <CloudsTab />
        </TabsContent>
        {isAdmin && isPartner && (
          <TabsContent value="partner" className="mt-4">
            <PartnerTab />
          </TabsContent>
        )}
        {isAdmin && (
          <TabsContent value="api-keys" className="mt-4">
            <ApiKeysTab />
          </TabsContent>
        )}
        {isAdmin && (
          <TabsContent value="integrations" className="mt-4">
            <WorkIntegrationsTab />
          </TabsContent>
        )}
        {isAdmin && (
          <TabsContent value="notifications" className="mt-4 flex flex-col gap-4">
            <NotificationsTab />
            <OutgoingWebhooksCard />
          </TabsContent>
        )}
        <TabsContent value="kubernetes" className="mt-4">
          <ClustersTab />
        </TabsContent>
        <TabsContent value="reliability" className="mt-4">
          <ReliabilityTab />
        </TabsContent>
        <TabsContent value="ownership" className="mt-4">
          <OwnershipTab />
        </TabsContent>
        <TabsContent value="members" className="mt-4 flex flex-col gap-4">
          <MembersTab />
          <TeamsCard />
          {isAdmin && <CustomRolesCard />}
          <ElevatedAccessCard />
          {isAdmin && <DomainsCard />}
        </TabsContent>
        {isAdmin && (
          <TabsContent value="sso" className="mt-4 flex flex-col gap-4">
            <SsoTab />
            <ScimCard />
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
