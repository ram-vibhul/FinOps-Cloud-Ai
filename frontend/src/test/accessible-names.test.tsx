import { act } from "@testing-library/react";
import { computeAccessibleName } from "dom-accessibility-api";
import { makeMe, mockApi, renderRoute } from "@/test/render";

/**
 * Accessibility audit (9.3): on the main pages, every button, link and form control a
 * keyboard or screen-reader user can reach must have a name. Pages render with an
 * otherwise empty API (unknown calls answer 404), which also checks the empty and
 * error states.
 */
const ROUTES = [
  "/", "/costs", "/costs/explore", "/costs/ai", "/savings", "/kubernetes", "/governance", "/executive",
  "/settings?tab=general", "/settings?tab=members", "/settings?tab=clouds", "/settings?tab=api-keys", "/setup", "/glossary", "/notifications",
];  // fmt: skip

const CONTROLS = "button, a[href], input:not([type=hidden]), select, textarea, [role=button], [role=link], [role=tab]";

describe.each(ROUTES)("accessible names on %s", (route) => {
  it("every control has a name", async () => {
    mockApi({ "GET /api/v1/auth/me": makeMe({ role: "owner" }) });
    const { container } = renderRoute(route);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    const unnamed = [...container.ownerDocument.querySelectorAll<HTMLElement>(CONTROLS)]
      .filter((el) => !el.closest("[hidden],[aria-hidden=true]"))
      .filter((el) => computeAccessibleName(el).trim() === "")
      .map((el) => el.outerHTML.slice(0, 160));
    expect(unnamed).toEqual([]);
  });
});
