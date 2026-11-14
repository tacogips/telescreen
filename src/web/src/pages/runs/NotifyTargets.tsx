import { For, Show, type JSX } from "solid-js";
import type { JsonValue, NotifyTargetCount } from "../../api/types";
import { Table } from "../../components/ui";

function count(value: JsonValue | undefined): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}
function targetCount(value: JsonValue): NotifyTargetCount | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const index = count(value.index); const queued = count(value.queued); const blocked = count(value.blocked); const skipped = count(value.skipped);
  if (index === null || queued === null || blocked === null || skipped === null || typeof value.channel !== "string") return null;
  return { index, channel: value.channel, queued, blocked, skipped };
}

/** Per-target counts of a v2 notify step output (`targets`); null for v1 outputs or malformed data. */
export function notifyTargets(output: JsonValue | null): readonly NotifyTargetCount[] | null {
  if (output === null || typeof output !== "object" || Array.isArray(output)) return null;
  const targets = output.targets;
  if (!Array.isArray(targets) || targets.length === 0) return null;
  const parsed = targets.map(targetCount);
  return parsed.every((item) => item !== null) ? parsed as NotifyTargetCount[] : null;
}

/** Small table of notification outcomes per target and channel. Counts only, never recipient values. */
export function NotifyTargets(props: Readonly<{ output: JsonValue | null }>): JSX.Element {
  return <Show when={notifyTargets(props.output)}>{(rows) => <div class="notify-targets"><h4>Notification targets</h4>
    <Table><thead><tr><th>Target</th><th>Channel</th><th>Queued</th><th>Blocked</th><th>Skipped</th></tr></thead><tbody>
      <For each={rows()}>{(row) => <tr><td>{row.index}</td><td>{row.channel}</td><td>{row.queued}</td><td>{row.blocked}</td><td>{row.skipped}</td></tr>}</For>
    </tbody></Table>
  </div>}</Show>;
}
