import { render, screen } from "@solidjs/testing-library";
import { MemoryRouter, Route } from "@solidjs/router";
import { describe, expect, it } from "vitest";
import { Sidebar } from "./Sidebar";

describe("Sidebar", () => {
  it("renders the ten areas in their declared order", () => {
    render(() => <MemoryRouter><Route path="*" component={Sidebar} /></MemoryRouter>);
    const links = screen.getAllByRole("link").map((link) => link.textContent);
    expect(links).toEqual(["Telescreen", "Issues", "Search", "Workflows", "Runs", "Police", "Collectors", "Escalation Policies", "Audit", "Assistant", "Settings"]);
  });
});
