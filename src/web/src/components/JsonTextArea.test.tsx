import { fireEvent, render, screen } from "@solidjs/testing-library";
import { describe, expect, it, vi } from "vitest";
import { JsonTextArea } from "./JsonTextArea";

describe("JsonTextArea", () => {
  it("reports validation paths and forwards edits", async () => {
    const onInput = vi.fn();
    render(() => <JsonTextArea value="{}" onInput={onInput} errors={[{ path: "steps.0", code: "unknown_step" }]} />);
    expect(screen.getByText("steps.0")).toBeInTheDocument();
    await fireEvent.input(screen.getByRole("textbox"), { target: { value: "{\"steps\":[]}" } });
    expect(onInput).toHaveBeenCalledWith("{\"steps\":[]}");
  });
});
