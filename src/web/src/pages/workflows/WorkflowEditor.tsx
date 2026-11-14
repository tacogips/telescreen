import { A, useNavigate } from "@solidjs/router";
import { createResource, For, Show, createSignal, type JSX } from "solid-js";
import { ApiError, workflowsApi } from "../../api/client";
import type { Workflow, WorkflowVersion } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { JsonTextArea } from "../../components/JsonTextArea";

type WorkflowSummary = Workflow & Readonly<{ latest_version?: number | null }>;
type WorkflowVersionIndex = Readonly<{ versions: WorkflowVersion[]; active_version: number | null }>;

export function WorkflowEditor(props: Readonly<{ name?: string; onChanged?: () => void }>): JSX.Element {
  const navigate = useNavigate();
  const [format, setFormat] = createSignal<"yaml" | "json">("yaml");
  const [source, setSource] = createSignal("");
  const [validationErrors, setValidationErrors] = createSignal<Readonly<{ path: string; code: string }[]>>([]);
  const [error, setError] = createSignal<ApiError | null>(null);
  const [notice, setNotice] = createSignal("");
  const [issueId, setIssueId] = createSignal("");
  const [busy, setBusy] = createSignal(false);
  const [schema] = createResource(() => workflowsApi.schema());
  const [versions, { refetch: refetchVersions }] = createResource(
    () => props.name === undefined || props.name === "new" ? undefined : props.name,
    async (name): Promise<WorkflowVersionIndex> => {
      const workflow = await workflowsApi.get(name);
      const latest = (workflow as WorkflowSummary).latest_version ?? workflow.active_version ?? 0;
      const versionNumbers = Array.from({ length: latest }, (_, index) => index + 1);
      const available = await Promise.all(versionNumbers.map((version) => workflowsApi.version(name, version)));
      return { versions: available, active_version: workflow.active_version };
    },
  );
  const [selectedVersion, setSelectedVersion] = createSignal<WorkflowVersion | null>(null);

  const loadVersion = async (version: number): Promise<void> => {
    if (props.name === undefined || props.name === "new") return;
    setError(null);
    try {
      const detail = await workflowsApi.version(props.name, version);
      setSelectedVersion(detail);
      setSource(detail.source_text);
      setFormat(detail.source_format);
      setNotice(`Loaded version ${version}`);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The version could not be loaded."));
    }
  };

  const validate = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    setNotice("");
    try {
      const result = await workflowsApi.validate(format(), source());
      setValidationErrors(result.errors);
      setNotice(result.valid ? "Workflow is valid." : "Review the validation errors.");
    } catch (cause) {
      setValidationErrors([]);
      setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "Validation could not be completed."));
    } finally {
      setBusy(false);
    }
  };

  const save = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    setNotice("");
    try {
      const created = await workflowsApi.create(format(), source());
      setSelectedVersion(created);
      setSource(created.source_text);
      setNotice(`Saved version ${created.version}.`);
      props.onChanged?.();
      if (props.name === undefined || props.name === "new") navigate(`/workflows/${encodeURIComponent(created.name)}`);
      else await refetchVersions();
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === "raw_value_detected") {
        setError(new ApiError(cause.status, cause.code, "Source contains sensitive values."));
      } else {
        setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The workflow could not be saved."));
      }
    } finally {
      setBusy(false);
    }
  };

  const activate = async (version: number): Promise<void> => {
    if (props.name === undefined || props.name === "new") return;
    setBusy(true);
    setError(null);
    try {
      await workflowsApi.activate(props.name, version);
      setNotice(`Version ${version} is active.`);
      await refetchVersions();
      props.onChanged?.();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The version could not be activated."));
    } finally {
      setBusy(false);
    }
  };

  const startRun = async (): Promise<void> => {
    if (props.name === undefined || props.name === "new") return;
    setBusy(true);
    setError(null);
    try {
      const run = await workflowsApi.startRun(props.name, issueId().trim() || undefined);
      navigate(`/runs/${encodeURIComponent(run.id)}`);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The run could not be started."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div class="workflow-editor">
      <div class="page-heading"><div><A href="/workflows">Workflows</A><h1>{props.name === undefined || props.name === "new" ? "New workflow" : props.name}</h1><p>Create a version, validate it, then activate it for issue triggers.</p></div>
        <Show when={schema()?.schema}><a href={schema()?.schema} target="_blank" rel="noreferrer">Workflow schema</a></Show>
      </div>
      <Show when={error()}>{(value) => <div class="error-banner" role="alert"><strong>{value().code}</strong><span>{value().detail}</span></div>}</Show>
      <Show when={notice()}>{(message) => <p class="notice" role="status">{message()}</p>}</Show>
      <Show when={schema.error}><ErrorBanner error={schema.error instanceof ApiError ? schema.error : new ApiError(0, "request_failed", "Schema could not be loaded.")} /></Show>
      <section class="panel editor-panel">
        <div class="editor-toolbar"><label class="field">Format<select value={format()} onChange={(event) => setFormat(event.currentTarget.value as "yaml" | "json")}><option value="yaml">YAML</option><option value="json">JSON</option></select></label>
          <div class="button-row"><button class="button-secondary" type="button" disabled={busy()} onClick={() => void validate()}>Validate</button><button class="button-primary" type="button" disabled={busy()} onClick={() => void save()}>Save as new version</button></div>
        </div>
        <JsonTextArea value={source()} onInput={setSource} errors={validationErrors()} label="Workflow source" />
      </section>
      <Show when={props.name !== undefined && props.name !== "new"}>
        <section class="panel"><h2>Versions</h2><Show when={!versions.loading} fallback={<p>Loading versions...</p>}>
          <Show when={versions()?.versions.length} fallback={<p>No versions saved yet.</p>}>
            <ul class="version-list"><For each={versions()?.versions ?? []}>{(version) => <li><span>Version {version.version} {version.active ? <strong>(active)</strong> : ""}</span><span><button class="button-secondary" type="button" disabled={busy()} onClick={() => void loadVersion(version.version)}>Open</button>{!version.active && <button class="button-secondary" type="button" disabled={busy()} onClick={() => void activate(version.version)}>Activate</button>}</span></li>}</For></ul>
          </Show>
        </Show></section>
        <section class="panel"><h2>Start a run</h2><label class="field">Issue ID (optional)<input value={issueId()} onInput={(event) => setIssueId(event.currentTarget.value)} placeholder="Issue ID" /></label><button class="button-primary" type="button" disabled={busy() || versions()?.active_version == null} onClick={() => void startRun()}>Start run</button><Show when={selectedVersion()}>{(version) => <span class="muted"> Editing version {version().version}</span>}</Show></section>
      </Show>
    </div>
  );
}
