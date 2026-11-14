import { createSignal, For, Show, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import { ApiError, searchApi } from "../../api/client";
import type { SearchHit, SearchResponse } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { RelativeTime } from "../../components/RelativeTime";
import { TopBar } from "../../components/shell/TopBar";
import { Button, EmptyState, Redacted, Table, Tag } from "../../components/ui";
import "./search.css";

type SearchIndex = "issues" | "findings" | "runs" | "audit";
const indexes: readonly SearchIndex[] = ["issues", "findings", "runs", "audit"];

function hrefFor(hit: SearchHit): string | undefined {
  if (hit.index === "issues") return `/issues/${encodeURIComponent(hit.id)}`;
  if (hit.index === "runs") return `/runs/${encodeURIComponent(hit.id)}`;
  return undefined;
}

export default function SearchPage(): JSX.Element {
  const [query, setQuery] = createSignal("");
  const [index, setIndex] = createSignal<SearchIndex>("issues");
  const [results, setResults] = createSignal<SearchResponse>();
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal<ApiError | null>(null);
  const submit = async (event: SubmitEvent): Promise<void> => {
    event.preventDefault();
    if (query().trim() === "") return;
    setLoading(true);
    setError(null);
    try {
      setResults(
        await searchApi.search(
          new URLSearchParams({ q: query().trim(), index: index() }),
        ),
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError(
              0,
              "request_failed",
              "The request could not be completed.",
            ),
      );
      setResults(undefined);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section class="page-content search-page">
      <TopBar.Slot name="title">Search</TopBar.Slot>
      <form class="search-form" onSubmit={(event) => void submit(event)}>
        <label>
          Search query
          <input
            aria-label="Search query"
            value={query()}
            onInput={(event) => setQuery(event.currentTarget.value)}
          />
        </label>
        <Button type="submit" variant="primary" busy={loading()}>
          Search
        </Button>
      </form>
      <nav class="search-index-tabs" role="tablist" aria-label="Search index">
        <For each={indexes}>
          {(value) => (
            <button
              type="button"
              role="tab"
              aria-selected={index() === value}
              onClick={() => setIndex(value)}
            >
              {value}
            </button>
          )}
        </For>
      </nav>
      <div class="search-page-actions">
        <A href={`/assistant?q=${encodeURIComponent(query())}`}>
          Ask Assistant
        </A>
      </div>
      <Show when={error()} keyed>
        {(value) => <ErrorBanner error={value} />}
      </Show>
      <Show when={loading()}>
        <p role="status">Searching…</p>
      </Show>
      <Show when={results()}>
        {(value) => (
          <>
            <p class="search-result-count">{value().total} results</p>
            <Show
              when={value().items.length > 0}
              fallback={
                <EmptyState title="No results found">
                  <p>Try another term or search index.</p>
                </EmptyState>
              }
            >
              <Table>
                <thead>
                  <tr>
                    <th>Result</th>
                    <th>Status</th>
                    <th>Time</th>
                  </tr>
                </thead>
                <tbody>
                  <For each={value().items}>
                    {(hit) => (
                      <tr>
                        <td>
                          <Show
                            when={hrefFor(hit)}
                            fallback={
                              <article class="search-hit">
                                <strong>{hit.title}</strong>
                                <Show when={hit.snippet !== null}>
                                  <p>
                                    <Redacted text={hit.snippet ?? ""} />
                                  </p>
                                </Show>
                              </article>
                            }
                          >
                            {(href) => (
                              <A class="search-hit" href={href()}>
                                <strong>{hit.title}</strong>
                                <Show when={hit.snippet !== null}>
                                  <p>
                                    <Redacted text={hit.snippet ?? ""} />
                                  </p>
                                </Show>
                              </A>
                            )}
                          </Show>
                        </td>
                        <td>
                          <Show when={hit.status !== null}>
                            <Tag variant="muted">{hit.status ?? ""}</Tag>
                          </Show>
                        </td>
                        <td>
                          {hit.at === null ? (
                            "—"
                          ) : (
                            <RelativeTime iso={hit.at} />
                          )}
                        </td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </Table>
            </Show>
          </>
        )}
      </Show>
    </section>
  );
}
