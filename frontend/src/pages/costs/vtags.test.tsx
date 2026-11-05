import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

describe("virtual tags", () => {
  it("builds rules, previews and saves", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/virtual-tags": [],
      "POST /api/v1/virtual-tags/preview": { values: [{ value: "data", cost: 50 }, { value: "unallocated", cost: 35 }], lines_by_source: {}, lines: 30 },
      "POST /api/v1/virtual-tags": { id: "vt1", key: "team", rules: [] },
    });
    renderRoute("/costs/virtual-tags");
    expect(await screen.findByText("No virtual tags yet")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /New virtual tag/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Rule 1: set value"), "data");
    await userEvent.type(within(dialog).getByLabelText("Rule 1 condition 1 values"), "444, 555");
    await userEvent.click(within(dialog).getByRole("button", { name: /And another condition/ }));
    await userEvent.selectOptions(within(dialog).getByLabelText("Rule 1 condition 2"), "tag");
    await userEvent.type(within(dialog).getByLabelText("Rule 1 condition 2 values"), "env=prod");
    await userEvent.click(within(dialog).getByRole("button", { name: "Preview" }));
    expect(await within(dialog).findByText("unallocated")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/virtual-tags")?.body).toEqual({
      key: "team", default_value: "unallocated", override_existing: false,
      rules: [{ value: "data", match: { account: ["444", "555"], tag: { env: ["prod"] } } }],
    }));
  });
});
