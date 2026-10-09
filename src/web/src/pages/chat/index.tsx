import { For, createSignal } from "solid-js";
import { ApiError, chatApi } from "../../api/client";
import type { ApiMessage } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";

export default function ChatPage() {
  const [messages, setMessages] = createSignal<ApiMessage[]>([]);
  const [input, setInput] = createSignal("");
  const [error, setError] = createSignal<ApiError | null>(null);
  const [sending, setSending] = createSignal(false);

  async function send(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const content = input().trim(); if (content.length === 0 || sending()) return;
    const transcript = [...messages(), { role: "user" as const, content }];
    setMessages(transcript); setInput(""); setError(null); setSending(true);
    try { const response = await chatApi.send(transcript); setMessages([...transcript, { role: "assistant", content: response.answer }]); }
    catch (caught) { setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed.")); }
    finally { setSending(false); }
  }

  return <section>
    <h1>Assistant</h1><p>Ask questions about masked incidents and workflow activity.</p><ErrorBanner error={error()} />
    <div class="chat-transcript" aria-live="polite"><For each={messages()}>{(message) => <article><strong>{message.role === "user" ? "You" : "Assistant"}</strong><pre>{message.content}</pre></article>}</For></div>
    <form onSubmit={(event) => void send(event)}><label class="field">Message<textarea value={input()} onInput={(event) => setInput(event.currentTarget.value)} /></label><button class="button-secondary" type="submit" disabled={sending()}>Send</button> <button class="button-secondary" type="button" onClick={() => { setMessages([]); setError(null); }}>Clear</button></form>
  </section>;
}
