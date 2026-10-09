import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { ErrorBanner } from "./ErrorBanner";

describe("ErrorBanner", () => {
  it("shows only the safe API code and detail", () => {
    render(() => <ErrorBanner error={new ApiError(422, "validation_error", "The input is invalid.")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("validation_error");
    expect(screen.getByRole("alert")).toHaveTextContent("The input is invalid.");
  });
});
