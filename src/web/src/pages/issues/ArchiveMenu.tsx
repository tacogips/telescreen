import { createSignal, type JSX } from "solid-js";
import { Button } from "../../components/ui";

export type ArchiveRequest = Readonly<{
  kind: "until_escalating" | "for_duration" | "until_findings" | "until_values";
  duration_minutes?: number;
  count?: number;
}>;

export function ArchiveMenu(props: Readonly<{ onArchive(request: ArchiveRequest): void }>): JSX.Element {
  const [count, setCount] = createSignal(5);
  const archive = (request: ArchiveRequest): void => props.onArchive(request);
  const threshold = (kind: "until_findings" | "until_values"): void => {
    archive({ kind, count: Math.min(100000, Math.max(1, Math.floor(count()))) });
  };
  return <details class="archive-menu">
    <summary>Archive</summary>
    <div role="group" aria-label="Archive conditions">
      <Button size="xs" onClick={() => archive({ kind: "until_escalating" })}>Until escalating</Button>
      <Button size="xs" onClick={() => archive({ kind: "for_duration", duration_minutes: 60 })}>For 1h</Button>
      <Button size="xs" onClick={() => archive({ kind: "for_duration", duration_minutes: 1440 })}>For 24h</Button>
      <Button size="xs" onClick={() => archive({ kind: "for_duration", duration_minutes: 10080 })}>For 7d</Button>
      <Button size="xs" onClick={() => archive({ kind: "for_duration", duration_minutes: 43200 })}>For 30d</Button>
      <Button size="xs" onClick={() => archive({ kind: "for_duration", duration_minutes: 129600 })}>For 90d</Button>
      <label>New findings<input aria-label="Archive finding threshold" type="number" min="1" max="100000" value={count()} onInput={(event) => setCount(Number(event.currentTarget.value))} /><Button size="xs" onClick={() => threshold("until_findings")}>Until N more findings</Button></label>
      <label>New values<input aria-label="Archive value threshold" type="number" min="1" max="100000" value={count()} onInput={(event) => setCount(Number(event.currentTarget.value))} /><Button size="xs" onClick={() => threshold("until_values")}>Until N more values</Button></label>
    </div>
  </details>;
}
