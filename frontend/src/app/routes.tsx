import type { RouteObject } from "react-router";
import { NotFoundPage } from "@/pages/not-found";
import { OverviewPage } from "@/pages/overview";
import { SectionPage } from "@/pages/section";
import { AppShell } from "./app-shell";
import { NAV_ITEMS } from "./nav";

export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      ...NAV_ITEMS.filter((i) => i.to !== "/").map((item) => ({
        path: item.to.slice(1),
        element: <SectionPage item={item} />,
      })),
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
