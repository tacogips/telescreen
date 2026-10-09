import { createSignal, For, Show, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import { ApiError, searchApi } from "../../api/client";
import type { SearchHit, SearchResponse } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";

type SearchIndex = "issues" | "findings" | "runs" | "audit";

export default function SearchPage(): JSX.Element {
  const [query, setQuery] = createSignal("");
  const [index, setIndex] = createSignal<SearchIndex>("issues");
  const [results, setResults] = createSignal<SearchResponse>();
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal<ApiError | null>(null);
  const submit = async (event: SubmitEvent): Promise<void> => {
    event.preventDefault();
    if (!query().trim()) return;
    setLoading(true);
    setError(null);
    try {
      setResults(await searchApi.search(new URLSearchParams({ q: query().trim(), index: index() })));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed."));
      setResults(undefined);
    } finally {
      setLoading(false);
    }
  };
  const hrefFor = (hit: SearchHit): string | undefined => hit.index === "issues" ? `/issues/${encodeURIComponent(hit.id)}` : hit.index === "runs" ? `/runs/${encodeURIComponent(hit.id)}` : undefined;
  return <section class="page-content">
    <div class="page-heading"><div><p class="eyebrow">Explore</p><h1>Search</h1></div></div>
    <form class="filters" onSubmit={submit}><label>Search query<input aria-label="Search query" value={query()} onInput={(event) => setQuery(event.currentTarget.value)} /></label><label>Index<select aria-label="Index" value={index()} onChange={(event) => setIndex(event.currentTarget.value as SearchIndex)}><option value="issues">Issues</option><option value="findings">Findings</option><option value="runs">Runs</option><option value="audit">Audit</option></select></label><button class="button-primary" type="submit">Search</button></form>
    <Show when={error()} keyed>{(value) => <ErrorBanner error={value} />}</Show>
    <Show when={loading()}><p>Searching…</p></Show>
    <Show when={results()}>{(value) => <><p>{value().total} results</p><ul class="search-results"><For each={value().items}>{(hit) => <li><Show when={hrefFor(hit)} fallback={<article><strong>{hit.title}</strong><p>{hit.snippet}</p></article>}>{(href) => <A href={href()}><strong>{hit.title}</strong><p>{hit.snippet}</p></A>}</Show><small>{hit.index} · {hit.status ?? ""}</small></li>}</For></ul></>}</Show>
  </section>;
}
