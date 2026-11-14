import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { Sparkline } from "./Sparkline";

describe("Sparkline", () => {
  it("renders MiniBarChart bars and an empty accessible chart", () => {
    const view = render(() => <Sparkline values={[1, 4, 2]} label="Issue events" />);
    expect(screen.getByRole("img", { name: "Issue events" }).querySelectorAll("rect")).toHaveLength(6);
    view.unmount();
    render(() => <Sparkline values={[]} />);
    expect(screen.getByRole("img", { name: "Recent activity" }).querySelectorAll("rect")).toHaveLength(0);
  });
});
