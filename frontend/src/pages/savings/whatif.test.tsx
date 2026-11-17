import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

const RESULT = (chosen: boolean) => ({
  currency: "USD", base_monthly: 1000, levers_monthly: chosen ? 150 : 0, total_monthly: chosen ? 150 : 0,
  after_monthly: chosen ? 850 : 1000, saving_pct: chosen ? 15 : null, commitment: null,
  levers: [
    { id: "graviton", label: "Move to Graviton (Arm)", count: 1, monthly: 50, chosen: false },
    { id: "schedule", label: "Stop non-production outside office hours", count: 1, monthly: 120, chosen },
    { id: "other", label: "Storage, database and network changes", count: 0, monthly: 0, chosen: false },
  ],
});

describe("what-if planner", () => {
  it("recalculates when a change is picked", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "POST /api/v1/what-if": (init?: RequestInit) => RESULT(String(init?.body).includes("schedule")),
    });
    renderRoute("/savings");
    const picks = await screen.findByRole("group", { name: "Changes to include" });
    expect(within(picks).queryByText(/Storage, database/)).not.toBeInTheDocument();
    await userEvent.click(within(picks).getByLabelText(/Stop non-production/));
    const result = screen.getByLabelText("What-if result");
    await waitFor(() => expect(result).toHaveTextContent("$850 (−15%)"));
    expect(calls.filter((c) => c.path === "/api/v1/what-if").at(-1)?.body).toEqual({ levers: ["schedule"], savings_plan: null });
  });
});
