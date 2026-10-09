import { For, Show, createSignal } from "solid-js";
import { ApiError, chatApi } from "../../api/client";
import type { ApiMessage } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { EmptyState, showToast } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";

export default function ChatPage() {
  const searchParams = new URLSearchParams(window.location.search);
  const context = searchParams.get("context");
  const [messages, setMessages] = createSignal<ApiMessage[]>([]);
  const [input, setInput] = createSignal(searchParams.get("q") ?? "");
  const [error, setError] = createSignal<ApiError | null>(null);
  const [sending, setSending] = createSignal(false);

  async function send(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const content = input().trim(); if (content.length === 0 || sending()) return;
    const contextual = context !== null && messages().length === 0 ? `Context: ${context}\n\n${content}` : content;
    const transcript = [...messages(), { role: "user" as const, content: contextual }];
    setMessages(transcript); setInput(""); setError(null); setSending(true);
    try { const response = await chatApi.send(transcript); setMessages([...transcript, { role: "assistant", content: response.answer }]); }
    catch (caught) { setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed.")); }
    finally { setSending(false); }
  }

  function clear(): void { setMessages([]); setError(null); setInput(""); showToast("Conversation cleared"); }

  return <section class="page-content assistant-page">
    <TopBar.Slot name="title">Assistant</TopBar.Slot>
    <TopBar.Slot name="actions"><button class="button-secondary" type="button" onClick={clear}>Clear</button></TopBar.Slot>
    {context !== null && <p class="context-chip"><span>Context:</span> <code>{context}</code></p>}
    <Show when={error()}>{(failure) => <ErrorBanner error={failure()} />}</Show>
    <div class="chat-transcript" aria-live="polite">
      <For each={messages()}>{(message) => <article class={`chat-message ${message.role}`}>
        <strong>{message.role === "user" ? "You" : "Assistant"}</strong><pre>{message.content}</pre>
      </article>}</For>
      {messages().length === 0 && <EmptyState title="Ask about incidents"><p>Responses use masked incident and workflow data.</p></EmptyState>}
    </div>
    <form class="assistant-composer" onSubmit={(event) => void send(event)}>
      <label class="field">Message<textarea value={input()} onInput={(event) => setInput(event.currentTarget.value)} placeholder="Ask a question…" /></label>
      <button class="button-primary" type="submit" disabled={sending()}>{sending() ? "Sending…" : "Send"}</button>
    </form>
  </section>;
}
