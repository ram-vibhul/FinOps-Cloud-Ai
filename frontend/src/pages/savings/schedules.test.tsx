import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

const SCHEDULE = {
  id: "s1", connection_id: "c1", name: "Staging", region: "ap-south-1", instance_ids: ["i-0000000a", "i-0000000b"],
  timezone: "Asia/Kolkata", days: [0, 1, 2, 3, 4], stop_at: "20:00", start_at: "08:00", status: "requested",
  savings_monthly: 0, requested_by: "dev@acme.com", approved_by: null, stopped_now: [], log: [],
};

describe("office-hours schedules", () => {
  it("lets an administrator approve a requested schedule", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "admin" }),
      "GET /api/v1/schedules": [SCHEDULE],
      "GET /api/v1/connections": [{ id: "c1", provider: "aws", name: "Prod" }],
      "POST /api/v1/schedules/s1/approve": { ...SCHEDULE, status: "approved" },
    });
    renderRoute("/savings");
    const list = await screen.findByRole("list", { name: "Office-hours schedules" });
    expect(within(list).getByText(/2 servers in ap-south-1, off 20:00–08:00 \(Asia\/Kolkata\)/)).toBeInTheDocument();
    await userEvent.click(within(list).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/schedules/s1/approve")).toBe(true));
  });

  it("sends a request with the instance IDs and days", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "member" }),
      "GET /api/v1/schedules": [],
      "GET /api/v1/connections": [{ id: "c1", provider: "aws", name: "Prod" }],
      "POST /api/v1/schedules": SCHEDULE,
    });
    renderRoute("/savings");
    await userEvent.click(await screen.findByRole("button", { name: "Add a schedule" }));
    const form = await screen.findByRole("form", { name: "Office-hours schedule" });
    await userEvent.type(within(form).getByLabelText("Name"), "Staging");
    await userEvent.type(within(form).getByLabelText("Region"), "ap-south-1");
    await userEvent.type(within(form).getByLabelText("Instance IDs"), "i-0000000a, i-0000000b");
    await userEvent.click(within(form).getByLabelText("Fri"));
    await userEvent.click(within(form).getByRole("button", { name: "Ask for approval" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/schedules")).toBe(true));
    const body = calls.find((c) => c.method === "POST" && c.path === "/api/v1/schedules")?.body as Record<string, unknown>;
    expect(body).toMatchObject({ connection_id: "c1", instance_ids: ["i-0000000a", "i-0000000b"], days: [0, 1, 2, 3] });
  });
});
