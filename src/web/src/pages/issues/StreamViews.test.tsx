import { cleanup, fireEvent, render, screen } from "@solidjs/testing-library";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SavedView } from "../../api/types";
import { StreamViews } from "./StreamViews";

const personal: SavedView = { id: "personal-1", name: "Mine", query: "is:unresolved", sort: "last_seen", visibility: "personal", starred: false, is_default: false };
const shared: SavedView = { id: "shared-1", name: "Team", query: "priority:high", sort: "trending", visibility: "shared", starred: true, is_default: false };

afterEach(cleanup);

describe("StreamViews", () => {
  it("groups server views, stars either visibility, sets defaults, and deletes only personal views", () => {
    const onApply = vi.fn();
    const onRemove = vi.fn();
    const onStar = vi.fn();
    const onDefault = vi.fn();
    render(() => <StreamViews views={[personal, shared]} activeViewId="personal-1" onApply={onApply} onRemove={onRemove} onStar={onStar} onDefault={onDefault} />);
    expect(screen.getByRole("region", { name: "Personal views" })).toHaveTextContent("Mine");
    expect(screen.getByRole("region", { name: "Shared views" })).toHaveTextContent("Team");
    expect(screen.getByRole("button", { name: "Delete view Mine" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete view Team" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Star view Mine" }));
    fireEvent.click(screen.getByRole("button", { name: "Set Mine as default" }));
    fireEvent.click(screen.getByRole("button", { name: "Set Team as default" }));
    expect(onStar).toHaveBeenCalledWith(personal);
    expect(onDefault).toHaveBeenCalledWith(personal);
    expect(onDefault).toHaveBeenCalledWith(shared);
  });
});
