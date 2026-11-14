import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { Unavailable } from "./Unavailable";

describe("Unavailable", () => {
  it("identifies the unavailable area", () => {
    render(() => <Unavailable label="Search" />);
    expect(screen.getByRole("heading", { name: "Search" })).toBeInTheDocument();
    expect(screen.getByText("This page is not available yet.")).toBeInTheDocument();
  });
});
