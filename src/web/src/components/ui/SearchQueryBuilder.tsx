import { For, Show, createSignal, type JSX } from "solid-js";
import { parseQuery, serializeTokens } from "./query";
import type { QueryKeySpec, QueryToken } from "./types";
export function SearchQueryBuilder(
  props: Readonly<{
    value: string;
    onChange(value: string): void;
    keys: readonly QueryKeySpec[];
    placeholder?: string;
  }>,
): JSX.Element {
  const tokens = (): QueryToken[] => parseQuery(props.value, props.keys);
  const [draft, setDraft] = createSignal("");
  const commit = (): void => {
    const text = draft().trim();
    if (text === "") return;
    props.onChange([props.value.trim(), text].filter(Boolean).join(" "));
    setDraft("");
  };
  return (
    <div class="query-builder">
      <div class="query-pills">
        <For each={tokens()}>
          {(token, tokenIndex) => (
            <span
              classList={{
                "query-token": true,
                invalid: token.kind === "filter" && !token.valid,
              }}
              title={token.kind === "filter" ? token.reason : undefined}
            >
              {token.kind === "text"
                ? token.value
                : `${token.key}:${token.value}`}
              <Show when={token.kind === "filter"}>
                {token.kind === "filter" ? (
                  <button
                    aria-label={`Remove filter: ${token.key}`}
                    onClick={() =>
                      props.onChange(
                        serializeTokens(
                          tokens().filter((_, index) => index !== tokenIndex()),
                        ),
                      )
                    }
                  >
                    ×
                  </button>
                ) : null}
              </Show>
            </span>
          )}
        </For>
        <input
          aria-label="Search query"
          placeholder={props.placeholder}
          value={draft()}
          onInput={(event) => setDraft(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              commit();
            } else if (
              event.key === "Backspace" &&
              event.currentTarget.value === ""
            ) {
              const buttons =
                event.currentTarget.parentElement?.querySelectorAll("button");
              buttons?.item(buttons.length - 1)?.focus();
            }
          }}
        />
      </div>
    </div>
  );
}
