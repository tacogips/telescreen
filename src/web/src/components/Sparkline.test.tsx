import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { Sparkline } from "./Sparkline";

describe("Sparkline", () => {
  it("renders values and a flat line for empty data", () => {
    const view = render(() => <Sparkline values={[1, 4, 2]} label="Issue events" />);
    expect(screen.getByRole("img", { name: "Issue events" }).querySelector("polyline")).toHaveAttribute("points", "0,14.5 50,1 100,10");
    view.unmount();
    render(() => <Sparkline values={[]} />);
    expect(screen.getByRole("img", { name: "Recent activity" }).querySelector("polyline")).toHaveAttribute("points", "0,10 100,10");
  });
});
