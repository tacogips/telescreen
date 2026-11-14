import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import ChatPage from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("sends the full transcript and renders the answer as literal text", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ answer: "first answer" }))).mockResolvedValueOnce(new Response(JSON.stringify({ answer: "<b>literal</b>" })));
  render(() => <ChatPage />);
  fireEvent.input(screen.getByLabelText("Message"), { target: { value: "first question" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("first answer");
  fireEvent.input(screen.getByLabelText("Message"), { target: { value: "second question" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const secondRequest = JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body)) as { messages: { role: string; content: string }[] };
  expect(secondRequest.messages.map((message) => message.content)).toEqual(["first question", "first answer", "second question"]);
  expect(await screen.findByText("<b>literal</b>")).toBeInTheDocument();
  expect(document.querySelector("b")).toBeNull();
});
