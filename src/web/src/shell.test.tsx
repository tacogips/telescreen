import { render, screen } from "@solidjs/testing-library";
import { MemoryRouter } from "@solidjs/router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Component } from "solid-js";
import { ApiError, apiFetch, setToken } from "./api/client";
import { Layout } from "./components/Layout";
import { AREAS, AppRoutes, AreaPage, type AreaKey } from "./routes";

afterEach(() => {
  vi.unstubAllGlobals();
  setToken(null);
});

describe("web shell", () => {
  it("renders all ten navigation areas in order", () => {
    render(() => <MemoryRouter root={Layout}><AppRoutes /></MemoryRouter>);
    const areaLabels = AREAS.map((area) => area.label);
    const labels = screen.getAllByRole("link").map((link) => link.textContent);
    expect(labels.slice(1)).toEqual(areaLabels);
  });

  it("shows the unavailable state when the injected page map lacks an area", () => {
    render(() => <AreaPage area={AREAS[1]!} pages={new Map()} />);
    expect(screen.getByRole("heading", { name: "Search" })).toBeInTheDocument();
    expect(screen.getByText("This page is not available yet.")).toBeInTheDocument();
  });

  it("renders a page from the injected page map", () => {
    const SearchPage: Component = () => <p>Search results</p>;
    const pageModules = new Map<AreaKey, Component>([["search", SearchPage]]);
    render(() => <AreaPage area={AREAS[1]!} pages={pageModules} />);
    expect(screen.getByText("Search results")).toBeInTheDocument();
  });

  it("decodes safe API error fields into ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "validation_error", detail: "The input is invalid." } }), { status: 422, headers: { "Content-Type": "application/json" } })));
    await expect(apiFetch("/api/issues")).rejects.toMatchObject({ name: "ApiError", status: 422, code: "validation_error", detail: "The input is invalid." } satisfies Partial<ApiError>);
  });

  it("sends the stored bearer token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    setToken("session-token");
    await apiFetch<{ status: string }>("/api/health");
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer session-token");
  });
});
