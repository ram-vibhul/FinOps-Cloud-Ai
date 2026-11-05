import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

const REPORT = {
  currency: "USD", taggable_cost: 1000, required_tags: ["team", "project"],
  coverage: [
    { key: "team", untagged_cost: 300, coverage_pct: 70, top_untagged_services: [] },
    { key: "project", untagged_cost: 500, coverage_pct: 50, top_untagged_services: [] },
  ],
  unallocated: { tag: "team", cost: 300, pct: 30 },
  top_unallocated_resources: [{ resource_id: "i-2", service: "EC2", cost: 200 }],
  inconsistent_keys: [{ canonical: "team", suggested: "team", variants: [{ key: "team", cost: 700 }, { key: "Team", cost: 200 }] }],
  inconsistent_values: [],
};

describe("data quality", () => {
  it("shows coverage and merges spelling variants", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/costs/data-quality": REPORT,
      "GET /api/v1/org/tag-rules": { rules: [] },
      "PUT /api/v1/org/tag-rules": { rules: [] },
      "POST /api/v1/org/tag-rules/apply": { status: "queued" },
    });
    renderRoute("/costs/data-quality");
    expect(await screen.findByText("70%")).toBeInTheDocument();
    expect(screen.getByText("30% of taggable spend")).toBeInTheDocument();
    expect(screen.getByText("i-2")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Merge into “team”" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      rules: [{ kind: "key", from: ["Team"], to: "team" }] }));
    expect(calls.some((c) => c.path === "/api/v1/org/tag-rules/apply")).toBe(true);
  });

  it("hides editing from viewers", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "viewer" }),
      "GET /api/v1/costs/data-quality": REPORT,
      "GET /api/v1/org/tag-rules": { rules: [] },
    });
    renderRoute("/costs/data-quality");
    expect(await screen.findByText("70%")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Merge/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Tag keys, separated by commas")).toBeDisabled();
  });
});
