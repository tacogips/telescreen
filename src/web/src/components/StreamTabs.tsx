import { For, type JSX } from "solid-js";
import type { StreamTab } from "../api/types";
const labels: readonly Readonly<{ key: StreamTab; label: string }>[] = [{ key: "unresolved", label: "Unresolved" }, { key: "for_review", label: "For review" }, { key: "regressed", label: "Regressed" }, { key: "escalating", label: "Escalating" }, { key: "archived", label: "Archived" }, { key: "resolved", label: "Resolved" }];
export function StreamTabs(props: Readonly<{ value: StreamTab; counts: Readonly<Partial<Record<StreamTab, number>>>; onChange: (tab: StreamTab) => void }>): JSX.Element {
  return <nav class="stream-tabs" aria-label="Issue status"> <For each={labels}>{(item) => <button type="button" role="tab" aria-selected={props.value === item.key} onClick={() => props.onChange(item.key)}>{item.label}<span class="count-badge">{props.counts[item.key] ?? 0}</span></button>}</For></nav>;
}
