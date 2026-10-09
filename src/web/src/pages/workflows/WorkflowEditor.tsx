import { A, useNavigate } from "@solidjs/router";
import { createResource, For, Show, createSignal, type JSX } from "solid-js";
import { ApiError, WorkflowValidationError, workflowsApi } from "../../api/client";
import type { WorkflowVersion, WorkflowVersionRef } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Button, Panel } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";
import { WorkflowGraph } from "./WorkflowGraph";
import { parseWorkflowGraph } from "./graph";
import "./workflow-editor.css";

type WorkflowVersionIndex = Readonly<{ versions: WorkflowVersion[]; active_version: number | null }>;

export function WorkflowEditor(props: Readonly<{ name?: string; onChanged?: () => void }>): JSX.Element {
  const navigate = useNavigate();
  const [format, setFormat] = createSignal<"yaml" | "json">("yaml");
  const [source, setSource] = createSignal("");
  const [validationErrors, setValidationErrors] = createSignal<Readonly<{ path: string; code: string }[]>>([]);
  const [error, setError] = createSignal<ApiError | null>(null);
  const [notice, setNotice] = createSignal("");
  const [issueId, setIssueId] = createSignal("");
  const [startedRun, setStartedRun] = createSignal<string | null>(null);
  const [busy, setBusy] = createSignal(false);
  const [schema] = createResource(() => workflowsApi.schema());
  const [versions, { refetch: refetchVersions }] = createResource(
    () => props.name === undefined || props.name === "new" ? undefined : props.name,
    async (name): Promise<WorkflowVersionIndex> => {
      const result = await workflowsApi.get(name);
      const latestVersion = Math.max(0, ...result.items.map((item) => item.version));
      const records = await Promise.all(Array.from({ length: latestVersion }, (_, index) => workflowsApi.version(name, index + 1)));
      return { versions: records, active_version: records.find((item) => item.active)?.version ?? null };
    },
  );
  const [selectedVersion, setSelectedVersion] = createSignal<WorkflowVersion | null>(null);
  const versionIndex = (): WorkflowVersionIndex | undefined => versions.error ? undefined : versions.latest;
  const graph = (): ReturnType<typeof parseWorkflowGraph> => parseWorkflowGraph(source(), format());
  const lineCount = (): number => Math.max(1, source().split("\n").length);

  const loadVersion = async (version: number): Promise<void> => {
    if (props.name === undefined || props.name === "new") return;
    setError(null);
    try {
      const detail = await workflowsApi.version(props.name, version);
      setSelectedVersion(detail); setSource(detail.source_text); setFormat(detail.source_format); setNotice(`Loaded version ${version}`);
    } catch (cause) { setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The version could not be loaded.")); }
  };
  const validate = async (): Promise<void> => {
    setBusy(true); setError(null); setNotice("");
    try {
      const result = await workflowsApi.validate(format(), source());
      setValidationErrors(result.errors); setNotice(result.valid ? "Workflow is valid." : "Review the validation errors.");
    } catch (cause) { setValidationErrors([]); setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "Validation could not be completed.")); }
    finally { setBusy(false); }
  };
  const save = async (): Promise<void> => {
    setBusy(true); setError(null); setNotice("");
    try {
      const created: WorkflowVersionRef = await workflowsApi.create(format(), source());
      const detail = await workflowsApi.version(created.name, created.version);
      setSelectedVersion(detail); setSource(detail.source_text); setFormat(detail.source_format); setNotice(`Saved version ${detail.version}.`);
      props.onChanged?.();
      if (props.name === undefined || props.name === "new") navigate(`/workflows/${encodeURIComponent(created.name)}`);
      else await refetchVersions();
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === "raw_value_detected") setError(new ApiError(cause.status, cause.code, "Source contains sensitive values."));
      else if (cause instanceof WorkflowValidationError) { setValidationErrors(cause.errors); setError(cause); }
      else if (cause instanceof ApiError) setError(cause);
      else setError(new ApiError(0, "request_failed", "The workflow could not be saved."));
    } finally { setBusy(false); }
  };
  const activate = async (version: number): Promise<void> => {
    if (props.name === undefined || props.name === "new") return;
    setBusy(true); setError(null);
    try { await workflowsApi.activate(props.name, version); setNotice(`Version ${version} is active.`); await refetchVersions(); props.onChanged?.(); }
    catch (cause) { setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The version could not be activated.")); }
    finally { setBusy(false); }
  };
  const startRun = async (): Promise<void> => {
    if (props.name === undefined || props.name === "new") return;
    setBusy(true); setError(null);
    try {
      const run = await workflowsApi.startRun(props.name, issueId().trim() || undefined);
      setStartedRun(run.run_id);
      navigate(`/runs/${encodeURIComponent(run.run_id)}`);
    }
    catch (cause) { setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The run could not be started.")); }
    finally { setBusy(false); }
  };

  return <div class="workflow-editor">
    <TopBar.Slot name="title">{props.name === undefined || props.name === "new" ? "New workflow" : props.name}</TopBar.Slot>
    <TopBar.Slot name="actions">
      <Button variant="default" disabled={busy()} onClick={() => void validate()}>Validate</Button>
      <Button variant="primary" disabled={busy()} onClick={() => void save()}>Save as new version</Button>
      <Show when={selectedVersion() && !selectedVersion()?.active}><Button disabled={busy()} onClick={() => void activate(selectedVersion()?.version ?? 0)}>Activate</Button></Show>
    </TopBar.Slot>
    <p class="muted">Validate, save a new version, then activate it for issue triggers.</p>
    <Show when={error()}>{(value) => <div class="error-banner" role="alert"><strong>{value().code}</strong><span>{value().code === "raw_value_detected" ? "Source contains sensitive values." : value().detail}</span></div>}</Show>
    <Show when={notice()}>{(message) => <p class="notice" role="status">{message()}</p>}</Show>
    <Show when={validationErrors().length > 0}><ul class="workflow-validation-errors validation-errors" aria-label="Validation errors"><For each={validationErrors()}>{(item) => <li><code>{item.path}</code>: {item.code}</li>}</For></ul></Show>
    <Panel>
      <div class="editor-toolbar"><label class="field">Format<select value={format()} onChange={(event) => setFormat(event.currentTarget.value as "yaml" | "json")}><option value="yaml">YAML</option><option value="json">JSON</option></select></label>
        <Show when={!schema.error && schema.latest}><a href="/api/workflows/schema" target="_blank" rel="noreferrer">Workflow schema</a></Show>
      </div>
      <div class="workflow-editor-columns">
        <div class="workflow-source-editor"><div class="workflow-line-numbers" aria-hidden="true"><For each={Array.from({ length: lineCount() }, (_, index) => index + 1)}>{(line) => <span>{line}</span>}</For></div>
          <label class="sr-only" for="workflow-source">Workflow source</label><textarea id="workflow-source" aria-label="Workflow source" spellcheck={false} value={source()} onInput={(event) => setSource(event.currentTarget.value)} />
        </div>
        <WorkflowGraph graph={graph()} />
      </div>
    </Panel>
    <Show when={props.name !== undefined && props.name !== "new"}>
      <Panel><h2>Versions</h2><Show when={!versions.error} fallback={<ErrorBanner error={versions.error instanceof ApiError ? versions.error : new ApiError(0, "request_failed", "Versions could not be loaded.")} />}><Show when={!versions.loading} fallback={<p>Loading versions...</p>}><Show when={versionIndex()?.versions.length} fallback={<p>No versions saved yet.</p>}>
        <ul class="version-list"><For each={versionIndex()?.versions ?? []}>{(version) => <li><span>Version {version.version} {version.active ? <strong>(active)</strong> : ""}</span><span><Button size="sm" disabled={busy()} onClick={() => void loadVersion(version.version)}>Open</Button>{!version.active && <Button size="sm" disabled={busy()} onClick={() => { setSelectedVersion(version); void activate(version.version); }}>Activate</Button>}</span></li>}</For></ul>
      </Show></Show></Show></Panel>
      <Panel><h2>Start a run</h2><label class="field">Issue ID (optional)<input value={issueId()} onInput={(event) => setIssueId(event.currentTarget.value)} placeholder="Issue ID" /></label><Button variant="primary" disabled={busy() || versionIndex()?.active_version == null} onClick={() => void startRun()}>Start run</Button><Show when={startedRun()}>{(id) => <A href={`/runs/${encodeURIComponent(id())}`}>View run {id()}</A>}</Show><Show when={selectedVersion()}>{(version) => <span class="muted"> Editing version {version().version}</span>}</Show></Panel>
    </Show>
  </div>;
}
