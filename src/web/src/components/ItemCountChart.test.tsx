import { render, screen, waitFor } from "@solidjs/testing-library";
import { createSignal } from "solid-js";
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
  it("renders points that arrive after mount and a one-point history", async () => {
    const [points, setPoints] = createSignal<readonly { at: string; item_count: number }[]>([]);
    const view = render(() => <ItemCountChart points={points()} label="Item history" />);
    setPoints([
      { at: "2026-10-01", item_count: 2 },
      { at: "2026-10-02", item_count: 4 },
      { at: "2026-10-03", item_count: 3 },
    ]);
    await waitFor(() => expect(view.container.querySelector("path")?.getAttribute("d")?.match(/H /g) ?? []).toHaveLength(2));
    expect(screen.queryByText("No item count history")).toBeNull();
    setPoints([{ at: "2026-10-01", item_count: 2 }]);
    await waitFor(() => expect(view.container.querySelector("path")?.getAttribute("d")).toContain("H 24"));
  });
  it("has a fixed pixel height so a few points cannot stretch it to the viewBox ratio", () => {
    const view = render(() => <ItemCountChart points={[{ at: "2026-10-01", item_count: 5 }, { at: "2026-10-02", item_count: 3 }, { at: "2026-10-03", item_count: 0 }]} label="Item history" height={96} />);
    const svg = view.container.querySelector("svg.item-count-chart");
    expect(svg?.getAttribute("height")).toBe("96");
    expect(svg?.getAttribute("viewBox")).toBe("0 0 48 96");
  });
});
