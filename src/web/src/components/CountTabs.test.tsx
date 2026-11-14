import { fireEvent, render, screen } from "@solidjs/testing-library";
import { describe, expect, it, vi } from "vitest";
import { CountTabs } from "./CountTabs";

describe("CountTabs", () => {
  it("renders counts and notifies when the selected tab changes", () => {
    const onChange = vi.fn();
    render(() => <CountTabs tabs={[{ value: "all", label: "All" }, { value: "overdue", label: "Overdue" }]} value="all" counts={{ all: 8, overdue: 2 }} onChange={onChange} label="Data tabs" />);
    expect(screen.getByRole("tab", { name: "All 8" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "All 8" }).textContent?.replace(/\s+/g, " ").trim()).toBe("All 8");
    expect(screen.getByText("2")).toHaveClass("count-badge");
    fireEvent.click(screen.getByRole("tab", { name: "Overdue 2" }));
    expect(onChange).toHaveBeenCalledWith("overdue");
  });
});
