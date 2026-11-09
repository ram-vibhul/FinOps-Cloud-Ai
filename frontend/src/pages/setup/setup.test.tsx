import { screen, within } from "@testing-library/react";
import { makeMe, mockApi, renderRoute } from "@/test/render";

const CHECKLIST = {
  steps: [
    { key: "connect_cloud", title: "Connect a cloud account", why: "Read-only role.", done: true, link: "/settings?tab=clouds" },
    { key: "first_costs", title: "See your first costs", why: "First import.", done: false, link: "/costs" },
    { key: "kubernetes", title: "Connect a Kubernetes cluster (optional)", why: "Per namespace.", done: false, link: "/settings?tab=kubernetes", optional: true },
  ],
  done: 1, total: 2, next: "first_costs", hidden: false,
};

describe("guided setup", () => {
  it("highlights the next step and links to it", async () => {
    mockApi({ "GET /api/v1/auth/me": makeMe(), "GET /api/v1/org/setup": CHECKLIST });
    renderRoute("/setup");
    expect(await screen.findByText("1 of 2 done.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Do this next" })).toHaveAttribute("href", "/costs");
    expect(screen.getByLabelText("Setup progress")).toHaveAttribute("value", "1");
  });

  it("shows the first savings as soon as they are found", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/org/setup": CHECKLIST,
      "GET /api/v1/org/setup/first-savings": {
        stage: "ready", count: 4, savings_monthly: 1020, currency: "USD", started_at: null, finished_at: null,
        top: [
          { id: "f1", title: "Smaller database", savings_monthly: 900, currency: "USD" },
          { id: "f2", title: "Delete unattached volumes", savings_monthly: 120, currency: "USD" },
        ],
      },
    });
    renderRoute("/setup");
    const panel = await screen.findByRole("region", { name: "First savings" });
    expect(within(panel).getByText(/First savings: about/)).toHaveTextContent("1,020");
    expect(within(panel).getByText("Smaller database")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "See all 4 savings" })).toHaveAttribute("href", "/savings");
  });

  it("says what is happening while the first import runs", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/org/setup": CHECKLIST,
      "GET /api/v1/org/setup/first-savings": {
        stage: "importing", count: 0, savings_monthly: 0, currency: "USD", top: [], started_at: null, finished_at: null,
      },
    });
    renderRoute("/setup");
    expect(await screen.findByText(/Importing your costs/)).toBeInTheDocument();
  });

  it("counts down the free trial", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/org/setup": CHECKLIST,
      "GET /api/v1/org/trial": { status: "ending", ends_at: "2026-11-12T12:00:00Z", days_left: 3 },
    });
    renderRoute("/setup");
    expect(await screen.findByText(/3 days left in your free trial/)).toBeInTheDocument();
  });
});
