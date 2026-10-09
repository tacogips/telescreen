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
});
