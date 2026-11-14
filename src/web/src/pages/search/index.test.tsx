import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { MemoryRouter } from "@solidjs/router";
import { afterEach, describe, expect, it, vi } from "vitest";
import SearchPage from "./index";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("SearchPage", () => {
  it("searches the selected index and renders linked results", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ items: [{ index: "issues", id: "issue-7", title: "Masked result", snippet: "masked text", status: "unresolved", at: null }], total: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(() => <MemoryRouter root={SearchPage} />);
    fireEvent.input(screen.getByLabelText("Search query"), { target: { value: "masked" } });
    fireEvent.change(screen.getByLabelText("Index"), { target: { value: "issues" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Masked result")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Masked result/ }).getAttribute("href")).toBe("/issues/issue-7");
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("index=issues"))).toBe(true));
  });

  it("renders findings results without linking the finding id as an issue", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ items: [{ index: "findings", id: "finding-9", title: "Credential finding", snippet: "masked finding snippet", status: null, at: null }], total: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(() => <MemoryRouter root={SearchPage} />);
    fireEvent.input(screen.getByLabelText("Search query"), { target: { value: "credential" } });
    fireEvent.change(screen.getByLabelText("Index"), { target: { value: "findings" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Credential finding")).toBeTruthy();
    expect(screen.getByText("masked finding snippet")).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Credential finding/ })).toBeNull();
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("index=findings"))).toBe(true));
    expect(document.querySelector('a[href="/issues/finding-9"]')).toBeNull();
  });

  it("renders a safe error code", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ error: { code: "unauthorized", detail: "Sign in required" } }), { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    render(() => <MemoryRouter root={SearchPage} />);
    fireEvent.input(screen.getByLabelText("Search query"), { target: { value: "query" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("unauthorized");
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});
