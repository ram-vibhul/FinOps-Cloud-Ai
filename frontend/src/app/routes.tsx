import { lazy, Suspense } from "react";
import type { RouteObject } from "react-router";
import { FullPageSpinner } from "@/components/spinner";

// Charts (ECharts) load only when a chart page is opened.
const CostsPage = lazy(() => import("@/pages/costs").then((m) => ({ default: m.CostsPage })));
const VirtualTagsPage = lazy(() => import("@/pages/costs/vtags").then((m) => ({ default: m.VirtualTagsPage })));
const ResourcesPage = lazy(() => import("@/pages/costs/resources").then((m) => ({ default: m.ResourcesPage })));
const DataQualityPage = lazy(() => import("@/pages/costs/quality").then((m) => ({ default: m.DataQualityPage })));
const ExplorerPage = lazy(() => import("@/pages/costs/explorer").then((m) => ({ default: m.ExplorerPage })));
import { InvitePage } from "@/pages/invite";
import { NotFoundPage } from "@/pages/not-found";
import { OverviewPage } from "@/pages/overview";
import { SectionPage } from "@/pages/section";
import { SettingsPage } from "@/pages/settings";
import { SignInPage } from "@/pages/sign-in";
import { WelcomePage } from "@/pages/welcome";
import { AppShell } from "./app-shell";
import { NAV_ITEMS } from "./nav";
import { RequireSession } from "./require-session";

export const routes: RouteObject[] = [
  { path: "/sign-in", element: <SignInPage /> },
  { path: "/invite/:token", element: <InvitePage /> },
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
      ...NAV_ITEMS.filter((i) => !["/", "/settings", "/costs"].includes(i.to)).map((item) => ({
        path: item.to.slice(1),
        element: <SectionPage item={item} />,
      })),
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
