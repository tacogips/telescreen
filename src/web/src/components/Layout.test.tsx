import { render, screen } from "@solidjs/testing-library";
import { MemoryRouter, Route } from "@solidjs/router";
import { describe, expect, it } from "vitest";
import { Layout } from "./Layout";

describe("Layout", () => {
  it("places the page content beside the navigation", () => {
    render(() => <MemoryRouter root={Layout}><Route path="*" component={() => <h1>Overview</h1>} /></MemoryRouter>);
    expect(screen.getByText("Reporting Bureau")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument();
  });
});
