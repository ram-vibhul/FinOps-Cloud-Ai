import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

describe("glossary", () => {
  it("lists plain-English meanings and filters them", async () => {
    mockApi({ "GET /api/v1/auth/me": makeMe() });
    renderRoute("/glossary");
    expect(await screen.findByText("Amortised cost")).toBeInTheDocument();
    expect(screen.getByText(/spreads it evenly over the year/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Search the glossary"), "graviton");
    expect(screen.getByText("Graviton (Arm)")).toBeInTheDocument();
    expect(screen.queryByText("Amortised cost")).not.toBeInTheDocument();
  });
});
