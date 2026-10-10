import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it.each([
    ["unresolved", "danger"],
    ["regressed", "danger"],
    ["in_progress", "info"],
    ["running", "info"],
    ["resolved", "success"],
    ["succeeded", "success"],
    ["ignored", "muted"],
    ["online", "muted"],
  ] as const)("maps %s to the %s tag variant", (status, variant) => {
    render(() => <StatusBadge status={status} />);
    const label = status.replaceAll("_", " ");
    expect(screen.getByText(label).closest(".ui-tag")).toHaveClass(variant);
  });

  it("labels the new statuses and warns for unverified resolution", () => {
    const view = render(() => <><StatusBadge status="allowed"/><StatusBadge status="pending_verification"/><StatusBadge status="resolved" resolution="manual_unverified"/></>);
    expect(screen.getByText("Allowed")).toBeInTheDocument();
    expect(screen.getByText("Verifying")).toBeInTheDocument();
    expect(screen.getByText("Resolved (unverified)").closest(".ui-tag")).toHaveClass("warning");
    view.unmount();
  });
});
