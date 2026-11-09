import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute, Reply } from "@/test/render";

describe("partner invitation", () => {
  it("creates a new organisation under the partner", async () => {
    const me = makeMe({ role: "member" });
    const { calls } = mockApi({
      "GET /api/v1/auth/me": me,
      "GET /api/v1/partner-invites/clp_abc": { partner: "Cloud Partners", logo: null, valid_days: 7 },
      "POST /api/v1/partner-invites/clp_abc/accept": { organization_id: "o9", partner: "Cloud Partners" },
    });
    renderRoute("/partner-invite/clp_abc");
    expect(await screen.findByText("Cloud Partners invites you")).toBeInTheDocument();
    expect(screen.getByLabelText("Organisation")).toHaveValue("o1"); // an organisation they own is offered first
    await userEvent.selectOptions(screen.getByLabelText("Organisation"), "new");
    await userEvent.type(screen.getByLabelText("Name"), "Startup");
    await userEvent.click(screen.getByRole("button", { name: "Accept invitation" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ new_name: "Startup" }));
  });

  it("explains an expired link", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/partner-invites/clp_old": new Reply(404, { title: "Not Found", status: 404, code: "bad_code", detail: "expired" }),
    });
    renderRoute("/partner-invite/clp_old");
    expect(await screen.findByText(/not valid or has expired/)).toBeInTheDocument();
  });
});
