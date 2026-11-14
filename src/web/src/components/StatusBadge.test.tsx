import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("formats status names and adds a status class", () => {
    const view = render(() => <StatusBadge status="in_progress" />);
    expect(screen.getByText("in progress")).toHaveClass("status-in-progress");
    view.unmount();
    render(() => <StatusBadge status="online" />);
    expect(screen.getByText("online")).toHaveClass("status-online");
  });
});
