import { For, Show, createResource, createSignal } from "solid-js";
import { ApiError, auditApi } from "../../api/client";
import type { AuditRecord, AuditSearchResponse } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { EmptyState, Pagination, Placeholder, Table, Tag, showToast } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";

const PAGE_SIZE = 50;

export default function AuditPage() {
  const [pageData, setPageData] = createSignal<AuditSearchResponse>({ items: [], total: 0 });
  const [error, setError] = createSignal<ApiError | null>(null);
  const [verifyResult, setVerifyResult] = createSignal<Readonly<{ label: string; ok: boolean }> | null>(null);
  const [query, setQuery] = createSignal("");
  const [action, setAction] = createSignal("");
  const [actor, setActor] = createSignal("");
  const [targetKind, setTargetKind] = createSignal("");
  const [from, setFrom] = createSignal("");
  const [to, setTo] = createSignal("");
  const [page, setPage] = createSignal(1);
  const [loading, setLoading] = createSignal(false);

  async function search(event?: SubmitEvent, requestedPage: number = 1): Promise<void> {
    event?.preventDefault(); setLoading(true); setError(null);
    const filters = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String((requestedPage - 1) * PAGE_SIZE) });
    if (query()) filters.set("q", query()); if (action()) filters.set("action", action());
    if (actor()) filters.set("actor", actor()); if (targetKind()) filters.set("target_kind", targetKind());
    if (from()) filters.set("from", new Date(from()).toISOString()); if (to()) filters.set("to", new Date(to()).toISOString());
    try { setPageData(await auditApi.search(filters)); setPage(requestedPage); }
    catch (caught) { setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed.")); }
    finally { setLoading(false); }
  }

  async function verify(): Promise<void> {
    setError(null); setVerifyResult(null);
    try {
      const result = await auditApi.verify();
      const label = result.ok ? `Chain verified up to #${result.checked}` : `Chain broken at #${result.first_broken_seq ?? "unknown"}`;
      setVerifyResult({ label, ok: result.ok });
      showToast(result.ok ? "Audit chain verified" : "Audit chain verification failed", result.ok ? "success" : "danger");
    } catch (caught) { setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed.")); }
  }

  const [initial] = createResource(async () => { await search(); return true; });
  const pages = () => Math.max(1, Math.ceil(pageData().total / PAGE_SIZE));

  return <section class="page-content">
    <TopBar.Slot name="title">Audit</TopBar.Slot>
    <TopBar.Slot name="actions"><button class="button-secondary" onClick={() => void verify()}>Verify chain</button></TopBar.Slot>
    <Show when={error()}>{(failure) => <ErrorBanner error={failure()} />}</Show>
    <Show when={verifyResult()}>{(result) => <p class={result().ok ? "notice-success" : "notice-danger"} role="status">{result().label}</p>}</Show>
    <form class="audit-filters" onSubmit={(event) => void search(event)}>
      <label class="field">Search<input value={query()} onInput={(event) => setQuery(event.currentTarget.value)} /></label>
      <label class="field">Action<input value={action()} onInput={(event) => setAction(event.currentTarget.value)} /></label>
      <label class="field">Actor<input value={actor()} onInput={(event) => setActor(event.currentTarget.value)} /></label>
      <label class="field">Target kind<input value={targetKind()} onInput={(event) => setTargetKind(event.currentTarget.value)} /></label>
      <label class="field">From<input type="datetime-local" value={from()} onInput={(event) => setFrom(event.currentTarget.value)} /></label>
      <label class="field">To<input type="datetime-local" value={to()} onInput={(event) => setTo(event.currentTarget.value)} /></label>
      <button class="button-primary" type="submit" disabled={loading()}>Search audit</button>
    </form>
    <Show when={!initial.loading} fallback={<Placeholder label="Loading audit records…" />}>
      <Show when={pageData().items.length > 0} fallback={<EmptyState title="No matching audit records"><p>Adjust the filters and search again.</p></EmptyState>}>
        <Table><thead><tr><th>Actor</th><th>Action</th><th>Target</th><th>Summary</th><th>Time</th></tr></thead><tbody>
          <For each={pageData().items}>{(record: AuditRecord) => <tr>
            <td><Tag>{record.actor_kind ?? "unknown"}</Tag><small>{record.actor_ref ?? "—"}</small></td>
            <td>{record.action ?? "—"}</td><td>{record.target_kind ?? "—"}: {record.target_id ?? "—"}</td>
            <td class="audit-summary">{record.summary ?? "—"}</td><td>{record.at ?? "—"}</td>
          </tr>}</For>
        </tbody></Table>
        <div class="audit-pagination"><span>{pageData().total} records</span><Pagination page={page()} pages={pages()} onChange={(nextPage) => void search(undefined, nextPage)} /></div>
      </Show>
    </Show>
  </section>;
}
