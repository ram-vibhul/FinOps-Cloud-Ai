import { act, renderHook } from "@testing-library/react";
import { usePoll } from "./use-poll";

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("usePoll", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    setHidden(false);
    vi.useRealTimers();
  });

  it("polls only while the tab is visible and refreshes when it comes back", () => {
    const fn = vi.fn<() => void>();
    renderHook(() => usePoll(fn, 1000));
    act(() => vi.advanceTimersByTime(3000));
    expect(fn).toHaveBeenCalledTimes(3);
    act(() => setHidden(true));
    act(() => vi.advanceTimersByTime(10_000));
    expect(fn).toHaveBeenCalledTimes(3); // nothing while hidden
    act(() => setHidden(false));
    expect(fn).toHaveBeenCalledTimes(4); // at once on return
    act(() => vi.advanceTimersByTime(1000));
    expect(fn).toHaveBeenCalledTimes(5);
  });

  it("can be switched off", () => {
    const fn = vi.fn<() => void>();
    renderHook(() => usePoll(fn, 1000, false));
    act(() => vi.advanceTimersByTime(5000));
    expect(fn).not.toHaveBeenCalled();
  });
});
