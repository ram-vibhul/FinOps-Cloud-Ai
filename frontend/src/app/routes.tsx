import { lazy, Suspense } from "react";
import type { RouteObject } from "react-router";
import { FullPageSpinner } from "@/components/spinner";

// Charts (ECharts) load only when a chart page is opened.
const CostsPage = lazy(() => import("@/pages/costs").then((m) => ({ default: m.CostsPage })));
const InvestigatePage = lazy(() => import("@/pages/kubernetes/investigate").then((m) => ({ default: m.InvestigatePage })));
const InvestigationPage = lazy(() => import("@/pages/kubernetes/investigate").then((m) => ({ default: m.InvestigationPage })));
const AiSpendPage = lazy(() => import("@/pages/costs/ai").then((m) => ({ default: m.AiSpendPage })));
const NetworkCostsPage = lazy(() => import("@/pages/costs/network").then((m) => ({ default: m.NetworkCostsPage })));
const CommitmentsPage = lazy(() => import("@/pages/savings/commitments-page").then((m) => ({ default: m.CommitmentsPage })));
const ExecutivePage = lazy(() => import("@/pages/executive").then((m) => ({ default: m.ExecutivePage })));
const GovernancePage = lazy(() => import("@/pages/governance").then((m) => ({ default: m.GovernancePage })));
const UpgradesPage = lazy(() => import("@/pages/savings/upgrades").then((m) => ({ default: m.UpgradesPage })));
const RoiPage = lazy(() => import("@/pages/savings/roi").then((m) => ({ default: m.RoiPage })));
const AuditPage = lazy(() => import("@/pages/savings/audit").then((m) => ({ default: m.AuditPage })));
const SavingsPage = lazy(() => import("@/pages/savings").then((m) => ({ default: m.SavingsPage })));
const KubernetesCostsPage = lazy(() => import("@/pages/costs/kubernetes").then((m) => ({ default: m.KubernetesCostsPage })));
const VirtualTagsPage = lazy(() => import("@/pages/costs/vtags").then((m) => ({ default: m.VirtualTagsPage })));
const ResourcesPage = lazy(() => import("@/pages/costs/resources").then((m) => ({ default: m.ResourcesPage })));
const DataQualityPage = lazy(() => import("@/pages/costs/quality").then((m) => ({ default: m.DataQualityPage })));
const ExplorerPage = lazy(() => import("@/pages/costs/explorer").then((m) => ({ default: m.ExplorerPage })));
import { GlossaryPage } from "@/pages/glossary";
import { NotificationsPage } from "@/pages/notifications";
import { InvitePage } from "@/pages/invite";
import { PartnerInvitePage } from "@/pages/partner-invite";
import { NotFoundPage } from "@/pages/not-found";
import { OverviewPage } from "@/pages/overview";
import { SectionPage } from "@/pages/section";
import { SettingsPage } from "@/pages/settings";
import { SetupPage } from "@/pages/setup";
import { SignInPage } from "@/pages/sign-in";
import { WelcomePage } from "@/pages/welcome";
import { AppShell } from "./app-shell";
import { NAV_ITEMS } from "./nav";
import { RequireSession } from "./require-session";

export const routes: RouteObject[] = [
  { path: "/sign-in", element: <SignInPage /> },
  { path: "/invite/:token", element: <InvitePage /> },
  {
    path: "/partner-invite/:code",
    element: (
      <RequireSession needsOrg={false}>
        <PartnerInvitePage />
      </RequireSession>
    ),
  },
  {
    path: "/welcome",
    element: (
      <RequireSession needsOrg={false}>
        <WelcomePage />
      </RequireSession>
    ),
  },
  {
    element: (
      <RequireSession>
        <AppShell />
      </RequireSession>
    ),
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "setup", element: <SetupPage /> },
      { path: "glossary", element: <GlossaryPage /> },
      { path: "notifications", element: <NotificationsPage /> },
      {
        path: "kubernetes/investigate",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading investigations" />}>
            <InvestigatePage />
          </Suspense>
        ),
      },
      {
        path: "kubernetes/investigations/:id",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading investigation" />}>
            <InvestigationPage />
          </Suspense>
        ),
      },
      {
        path: "costs/ai",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading AI spend" />}>
            <AiSpendPage />
          </Suspense>
        ),
      },
      {
        path: "costs/network",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading network costs" />}>
            <NetworkCostsPage />
          </Suspense>
        ),
      },
      {
        path: "savings/commitments",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading commitments" />}>
            <CommitmentsPage />
          </Suspense>
        ),
      },
      {
        path: "executive",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading executive summary" />}>
            <ExecutivePage />
          </Suspense>
        ),
      },
      {
        path: "governance",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading governance" />}>
            <GovernancePage />
          </Suspense>
        ),
      },
      {
        path: "savings/upgrades",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading upgrades" />}>
            <UpgradesPage />
          </Suspense>
        ),
      },
      {
        path: "savings/roi",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading ROI pack" />}>
            <RoiPage />
          </Suspense>
        ),
      },
      {
        path: "savings/audit",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading audit" />}>
            <AuditPage />
          </Suspense>
        ),
      },
      {
        path: "savings",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading savings" />}>
            <SavingsPage />
          </Suspense>
        ),
      },
      {
        path: "kubernetes",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading Kubernetes costs" />}>
            <KubernetesCostsPage />
          </Suspense>
        ),
      },
      {
        path: "costs/virtual-tags",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading virtual tags" />}>
            <VirtualTagsPage />
          </Suspense>
        ),
      },
      {
        path: "costs/resources",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading resources" />}>
            <ResourcesPage />
          </Suspense>
        ),
      },
      {
        path: "costs/data-quality",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading data quality" />}>
            <DataQualityPage />
          </Suspense>
        ),
      },
      {
        path: "costs/explore",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading explorer" />}>
            <ExplorerPage />
          </Suspense>
        ),
      },
      {
        path: "costs",
        element: (
          <Suspense fallback={<FullPageSpinner label="Loading costs" />}>
            <CostsPage />
          </Suspense>
        ),
      },
      ...NAV_ITEMS.filter((i) => !["/", "/settings", "/costs", "/kubernetes", "/savings", "/governance", "/executive"].includes(i.to)).map((item) => ({
        path: item.to.slice(1),
        element: <SectionPage item={item} />,
      })),
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
