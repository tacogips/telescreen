import { For, type JSX } from "solid-js";

export function JsonTextArea(props: Readonly<{ value: string; onInput: (value: string) => void; errors: Readonly<{ path: string; code: string }[]>; label?: string }>): JSX.Element {
  return (
    <label class="field json-field">
      <span>{props.label ?? "JSON or YAML"}</span>
      <textarea value={props.value} onInput={(event) => props.onInput(event.currentTarget.value)} spellcheck={false} />
      <ul class="validation-errors" aria-live="polite">
        <For each={props.errors}>{(error) => <li><code>{error.path || "$"}</code>: {error.code}</li>}</For>
      </ul>
    </label>
  );
}
