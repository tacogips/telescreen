import { fireEvent, render, screen } from "@solidjs/testing-library";
import { describe, expect, it, vi } from "vitest";
import { LoadMore } from "./LoadMore";

describe("LoadMore", () => {
  it("disables when no page remains and calls the handler otherwise", async () => {
    const onMore = vi.fn();
    const view = render(() => <LoadMore onMore={onMore} hasMore={false} />);
    expect(screen.getByRole("button", { name: "Load more" })).toBeDisabled();
    view.unmount();
    render(() => <LoadMore onMore={onMore} hasMore />);
    await fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(onMore).toHaveBeenCalledOnce();
  });
});
