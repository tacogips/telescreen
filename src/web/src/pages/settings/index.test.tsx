import { cleanup, fireEvent, render, screen } from "@solidjs/testing-library";
import { afterEach, expect, it } from "vitest";
import SettingsPage from "./index";

afterEach(() => { cleanup(); sessionStorage.clear(); });

it("stores the token in sessionStorage without showing it again", async () => {
  render(() => <SettingsPage />);
  fireEvent.input(screen.getByLabelText("Bearer API token"), { target: { value: "private-token-value" } });
  fireEvent.click(screen.getByRole("button", { name: "Save token" }));
  expect(sessionStorage.getItem("telescreen.token")).toBe("private-token-value");
  expect(await screen.findByText("Token set")).toBeInTheDocument();
  expect(screen.queryByText("private-token-value")).toBeNull();
});
