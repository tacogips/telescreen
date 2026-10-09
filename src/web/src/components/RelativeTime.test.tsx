import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { RelativeTime } from "./RelativeTime";

describe("RelativeTime", () => {
  it("renders valid dates as machine-readable times and handles invalid values", () => {
    const view = render(() => <RelativeTime iso="2026-10-08T00:00:00Z" />);
    expect(view.container.querySelector("time")).toHaveAttribute("datetime", "2026-10-08T00:00:00.000Z");
    expect(view.container.querySelector("time")).toHaveAttribute("title");
    view.unmount();
    render(() => <RelativeTime iso="invalid" />);
    expect(screen.getByText("Unknown time")).toBeInTheDocument();
  });
});
