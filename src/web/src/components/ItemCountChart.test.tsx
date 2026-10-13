import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { ItemCountChart } from "./ItemCountChart";

describe("ItemCountChart", () => {
  it("draws a step path for points", () => {
    const view = render(() => <ItemCountChart points={[{ at: "2026-10-01", item_count: 2 }, { at: "2026-10-02", item_count: 4 }]} label="Item history" />);
    expect(screen.getByRole("img", { name: "Item history" })).toBeInTheDocument();
    expect(view.container.querySelector("path")?.getAttribute("d")).toContain("H 24 V");
  });
  it("shows an empty state", () => {
    render(() => <ItemCountChart points={[]} label="Item history" />);
    expect(screen.getByText("No item count history")).toBeInTheDocument();
  });
});
