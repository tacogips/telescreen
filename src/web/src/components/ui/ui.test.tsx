import { render, screen, fireEvent } from "@solidjs/testing-library";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createSignal } from "solid-js";
import {
  Button,
  Drawer,
  FoldSection,
  MiniBarChart,
  PriorityBadge,
  parseQuery,
  Redacted,
  SearchQueryBuilder,
  SortSelect,
  SplitButton,
  serializeTokens,
  splitMasked,
} from "./index";

const keys = [
  { key: "is", values: ["unresolved", "resolved"], description: "status" },
  { key: "severity", description: "severity" },
];
const storageValues = new Map<string, string>();
const storage = {
  getItem: (key: string): string | null => storageValues.get(key) ?? null,
  setItem: (key: string, value: string): void => {
    storageValues.set(key, value);
  },
  removeItem: (key: string): void => {
    storageValues.delete(key);
  },
  clear: (): void => storageValues.clear(),
};
vi.stubGlobal("localStorage", storage);
afterEach(() => {
  storage.clear();
});

describe("design system primitives", () => {
  it("marks a busy button disabled and busy", () => {
    render(() => <Button busy>Save</Button>);
    expect(screen.getByRole("button")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("parses invalid query syntax and duplicate keys with reasons", () => {
    const result = parseQuery(
      "is:unresolved foo !is:resolved severity:[high, medium] times_seen:>5 collector:x",
      keys,
    );
    expect(result[0]).toMatchObject({ kind: "filter", valid: true });
    expect(result[1]).toEqual({ kind: "text", value: "foo" });
    const invalid = result.filter(
      (token) => token.kind === "filter" && !token.valid,
    );
    expect(invalid).toHaveLength(4);
    expect(
      invalid.every(
        (token) =>
          token.kind === "filter" &&
          typeof token.reason === "string" &&
          token.reason.length > 0,
      ),
    ).toBe(true);
    const duplicate = parseQuery("is:unresolved is:resolved", keys);
    expect(duplicate[1]).toMatchObject({
      kind: "filter",
      valid: false,
      reason: "This filter key is already used",
    });
    expect(serializeTokens(parseQuery("is:unresolved foo", keys))).toBe(
      "is:unresolved foo",
    );
  });

  it("commits multi-character query drafts once and clears the input", () => {
    const [value, setValue] = createSignal("is:unresolved");
    render(() => (
      <SearchQueryBuilder value={value()} onChange={setValue} keys={keys} />
    ));
    const input = screen.getByRole("textbox", { name: "Search query" });
    fireEvent.input(input, { target: { value: "a" } });
    expect(value()).toBe("is:unresolved");
    fireEvent.input(input, { target: { value: "ab" } });
    expect(value()).toBe("is:unresolved");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(value()).toBe("is:unresolved ab");
    expect(input).toHaveValue("");
    expect(screen.getByText("ab")).toBeInTheDocument();
  });

  it("keeps committed invalid query text visible with its reason", () => {
    const [value, setValue] = createSignal("is:unresolved");
    render(() => (
      <SearchQueryBuilder value={value()} onChange={setValue} keys={keys} />
    ));
    const input = screen.getByRole("textbox", { name: "Search query" });
    fireEvent.input(input, { target: { value: "unknown:value" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(value()).toBe("is:unresolved unknown:value");
    expect(screen.getByTitle("Supported keys: is, severity")).toHaveClass(
      "invalid",
    );
  });

  it("splits each supported masked form and plain text", () => {
    for (const sample of [
      "[ADDRESS REDACTED]",
      "**** **** **** 1111",
      "-----BEGIN PRIVATE KEY----- [REDACTED 1675 chars]",
    ]) {
      expect(splitMasked(sample).some((part) => part.masked)).toBe(true);
    }
    expect(splitMasked("ghp_****************[40]")).toEqual([
      { masked: false, text: "ghp_" },
      { masked: true, text: "****************[40]" },
    ]);
    expect(splitMasked("plain")).toEqual([{ masked: false, text: "plain" }]);
    render(() => (
      <Redacted text="token [REDACTED 20 chars]" ruleClass="credential" />
    ));
    expect(screen.getByTitle("Masked (rule: credential)")).toHaveTextContent(
      "[REDACTED 20 chars]",
    );
  });

  it("removes a query pill and focuses the last pill on empty backspace", () => {
    const [value, setValue] = createSignal("is:unresolved severity:high");
    render(() => (
      <SearchQueryBuilder value={value()} onChange={setValue} keys={keys} />
    ));
    const input = screen.getByRole("textbox", { name: "Search query" });
    input.focus();
    fireEvent.keyDown(input, { key: "Backspace" });
    expect(document.activeElement).toBe(
      screen.getAllByRole("button", { name: /Remove filter/ })[1],
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove filter: is" }));
    expect(value()).toBe("severity:high");
  });

  it("closes the drawer on Escape and traps focus at its ends", () => {
    let closed = false;
    render(() => (
      <Drawer
        open
        onClose={() => {
          closed = true;
        }}
        title="Details"
      >
        <button>Inside</button>
      </Drawer>
    ));
    const close = screen.getByRole("button", { name: "Close" });
    const inside = screen.getByRole("button", { name: "Inside" });
    inside.focus();
    fireEvent.keyDown(inside, { key: "Tab" });
    expect(document.activeElement).toBe(close);
    fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(inside);
    fireEvent.keyDown(inside, { key: "Escape" });
    expect(closed).toBe(true);
  });

  it("renders chart bars and persists collapsed sections across remounts", () => {
    const first = render(() => (
      <>
        <MiniBarChart values={[1, 3]} label="Finding counts" />
        <FoldSection id="sample" title="Details">
          <p>Body</p>
        </FoldSection>
      </>
    ));
    expect(
      screen
        .getByRole("img", { name: "Finding counts" })
        .querySelectorAll("rect"),
    ).toHaveLength(4);
    const toggle = screen.getByRole("button", { name: "Details" });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(storage.getItem("telescreen.fold.sample")).toBe("false");
    first.unmount();
    const restored = render(() => (
      <FoldSection id="sample" title="Details">
        <p>Body</p>
      </FoldSection>
    ));
    expect(screen.getByRole("button", { name: "Details" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByText("Body")).not.toBeInTheDocument();
    restored.unmount();
  });

  it("toggles the SplitButton menu outside the buttons and closes it", () => {
    const { container } = render(() => (
      <SplitButton menu={<button role="menuitem">Resolve options</button>}>
        Resolve
      </SplitButton>
    ));
    const toggle = screen.getByRole("button", { name: "More actions" });
    expect(toggle).toHaveAttribute("aria-haspopup", "menu");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const menu = screen.getByRole("menu");
    expect(toggle.contains(menu)).toBe(false);
    expect(container.querySelector("button [role=menu]")).toBeNull();
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("menuitem", { name: "Resolve options" }));
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("shows SortSelect labels and detail and reports the selected value", () => {
    let selected = "last_seen";
    render(() => (
      <SortSelect
        value={selected}
        options={[
          {
            value: "last_seen",
            label: "Last seen",
            detail: "Last time the item was found",
          },
          {
            value: "first_seen",
            label: "First seen",
            detail: "When the issue was first reported",
          },
        ]}
        onChange={(value) => {
          selected = value;
        }}
      />
    ));
    const sort = screen.getByRole("combobox", { name: "Sort" });
    expect(sort).toHaveTextContent("Last seen");
    expect(sort).toHaveTextContent("First seen");
    expect(
      screen.getByText("Last time the item was found"),
    ).toBeInTheDocument();
    fireEvent.change(sort, { target: { value: "first_seen" } });
    expect(selected).toBe("first_seen");
  });

  it("renders muted signal badges for every priority level", () => {
    for (const level of ["high", "medium", "low"] as const) {
      const rendered = render(() => <PriorityBadge level={level} />);
      const tag = rendered.container.querySelector(".ui-tag");
      expect(tag).toHaveClass("muted");
      expect(tag).toHaveTextContent(level);
      expect(tag?.querySelector("svg.priority-signal")).toBeInTheDocument();
      rendered.unmount();
    }
  });
});
