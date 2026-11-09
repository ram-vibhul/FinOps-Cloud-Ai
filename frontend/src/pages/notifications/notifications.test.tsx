import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

const NOW = new Date().toISOString();
const ITEMS = [
  {
    id: "n1", event: "budget_alert", title: "Platform: 90% of the budget used", text: "Spent 900 of 1,000 USD.",
    link: "/governance?tab=budgets", severity: "alert", organization_id: "o1", organization: "Acme",
    created_at: NOW, read_at: null,
  },
  {
    id: "n2", event: "invitation", title: "Ravi invited you to Globex", text: "Open the link in the email.",
    link: null, severity: "info", organization_id: "o2", organization: "Globex",
    created_at: "2026-01-02T10:00:00Z", read_at: "2026-01-02T11:00:00Z",
  },
];
const PREFS = [
  { event: "budget_alert", label: "Budget alerts", inbox: true, email: false },
  { event: "weekly_digest", label: "Weekly digest", inbox: true, email: false },
];

function api(extra = {}) {
  return mockApi({
    "GET /api/v1/auth/me": makeMe(),
    "GET /api/v1/me/notifications/unread": { unread: 1 },
    "GET /api/v1/me/notifications": { items: ITEMS, unread: 1, next_before: null },
    "POST /api/v1/me/notifications/n1/read": null,
    "POST /api/v1/me/notifications/read-all": { unread: 0 },
    "GET /api/v1/me/notification-preferences": PREFS,
    "PUT /api/v1/me/notification-preferences": (init: RequestInit | undefined) => JSON.parse(String(init?.body)),
    ...extra,
  });
}

describe("notification centre", () => {
  it("shows the unread badge and the latest notifications in the header", async () => {
    const { calls } = api();
    renderRoute("/glossary");
    const bell = await screen.findByRole("button", { name: "Notifications, 1 unread" });
    await userEvent.click(bell);
    const menu = await screen.findByRole("menu");
    expect(await within(menu).findByText("Platform: 90% of the budget used")).toBeInTheDocument();
    expect(within(menu).getByText("Ravi invited you to Globex")).toBeInTheDocument();
    await userEvent.click(within(menu).getByText("Platform: 90% of the budget used"));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/me/notifications/n1/read")).toBe(true));
    expect(await screen.findByRole("button", { name: "Notifications" })).toBeInTheDocument();
  });

  it("lists, marks read and saves personal choices", async () => {
    const { calls } = api();
    renderRoute("/notifications");
    expect(await screen.findByText("Spent 900 of 1,000 USD.")).toBeInTheDocument();
    expect(screen.getByText("1 unread")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", "/governance?tab=budgets");
    await userEvent.click(screen.getByRole("button", { name: "Mark read: Platform: 90% of the budget used" }));
    expect(await screen.findByText("0 unread")).toBeInTheDocument();

    await userEvent.click(await screen.findByRole("checkbox", { name: "Weekly digest in inbox" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Budget alerts by email" }));
    await userEvent.click(screen.getByRole("button", { name: "Save choices" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual([
        { event: "budget_alert", label: "Budget alerts", inbox: true, email: true },
        { event: "weekly_digest", label: "Weekly digest", inbox: false, email: false },
      ]),
    );
  });

  it("filters to unread and marks everything read", async () => {
    const { calls } = api();
    renderRoute("/notifications");
    await userEvent.click(await screen.findByRole("button", { name: "Unread only" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.path === "/api/v1/me/notifications").length).toBeGreaterThanOrEqual(2),
    );
    await userEvent.click(screen.getByRole("button", { name: "Mark all read" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/me/notifications/read-all")).toBe(true));
  });
});
