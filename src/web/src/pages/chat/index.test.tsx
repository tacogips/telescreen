import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import ChatPage from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });

it("sends the transcript and renders assistant content literally", async () => {
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

it("prefills q and sends page context with the first user message", async () => {
  window.history.replaceState({}, "", "/assistant?context=%2Fissues%2Fiss-01&q=Summarize+this+issue");
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ answer: "Summary" })));
  render(() => <ChatPage />);
  expect(screen.getByText("/issues/iss-01")).toBeInTheDocument();
  expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe("Summarize this issue");
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("Summary");
  const request = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)) as { messages: { content: string }[] };
  expect(request.messages[0]?.content).toBe("Context: /issues/iss-01\n\nSummarize this issue");
});

it("shows the API error when sending a message fails", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
    new Response(JSON.stringify({ error: { code: "internal_error", detail: "failed" } }), { status: 500 }),
  );
  render(() => <ChatPage />);
  fireEvent.input(screen.getByLabelText("Message"), { target: { value: "Check this incident" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("internal_error");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/chat");
});
