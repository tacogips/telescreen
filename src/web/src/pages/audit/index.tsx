import { For, Show, createResource, createSignal } from "solid-js";
import { ApiError, auditApi } from "../../api/client";
import type { AuditRecord } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";

export default function AuditPage() {
  const [records, setRecords] = createSignal<AuditRecord[]>([]);
  const [error, setError] = createSignal<ApiError | null>(null);
  const [verifyResult, setVerifyResult] = createSignal<string | null>(null);
  const [query, setQuery] = createSignal("");
  const [action, setAction] = createSignal("");
  const [actor, setActor] = createSignal("");
  const [targetKind, setTargetKind] = createSignal("");
  const [from, setFrom] = createSignal("");
  const [to, setTo] = createSignal("");
  const [loading, setLoading] = createSignal(false);
  const [initial] = createResource(async () => { await search(); return true; });

  async function search(event?: SubmitEvent): Promise<void> {
    event?.preventDefault(); setLoading(true); setError(null);
    const filters = new URLSearchParams();
    if (query()) filters.set("q", query()); if (action()) filters.set("action", action());
    if (actor()) filters.set("actor", actor()); if (targetKind()) filters.set("target_kind", targetKind());
    if (from()) filters.set("from", from()); if (to()) filters.set("to", to());
    try { setRecords((await auditApi.search(filters)).items); }
    catch (caught) { setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed.")); }
    finally { setLoading(false); }
  }

  async function verify(): Promise<void> {
    setError(null); setVerifyResult(null);
    try {
      const result = await auditApi.verify();
      setVerifyResult(result.ok ? "Chain intact" : `Broken at seq ${result.first_broken_seq ?? "unknown"}`);
    } catch (caught) { setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed.")); }
  }

  return <section>
    <h1>Audit</h1><ErrorBanner error={error()} />
    <form onSubmit={(event) => void search(event)}>
      <label class="field">Search<input value={query()} onInput={(event) => setQuery(event.currentTarget.value)} /></label>
      <label class="field">Action<input value={action()} onInput={(event) => setAction(event.currentTarget.value)} /></label>
      <label class="field">Actor<input value={actor()} onInput={(event) => setActor(event.currentTarget.value)} /></label>
      <label class="field">Target kind<input value={targetKind()} onInput={(event) => setTargetKind(event.currentTarget.value)} /></label>
      <label class="field">From<input type="datetime-local" value={from()} onInput={(event) => setFrom(event.currentTarget.value)} /></label>
      <label class="field">To<input type="datetime-local" value={to()} onInput={(event) => setTo(event.currentTarget.value)} /></label>
      <button class="button-secondary" type="submit" disabled={loading()}>Search</button> <button class="button-secondary" type="button" onClick={() => void verify()}>Verify chain</button>
      <Show when={verifyResult()}>{(result) => <p role="status">{result()}</p>}</Show>
    </form>
    <Show when={!initial.loading} fallback={<p>Loading audit records…</p>}>
      <table><thead><tr><th>Seq</th><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Summary</th></tr></thead><tbody>
        <For each={records()} fallback={<tr><td colspan="6">No matching audit records.</td></tr>}>
          {(record) => <tr><td>{record.seq}</td><td>{record.at}</td><td>{record.actor_ref ?? record.actor_kind}</td><td>{record.action}</td><td>{record.target_kind}: {record.target_id}</td><td>{record.summary}</td></tr>}
        </For>
      </tbody></table>
    </Show>
  </section>;
}
