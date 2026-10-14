import type { JSX } from "solid-js";

export type CountTab<T extends string> = Readonly<{ value: T; label: string }>;
export function CountTabs<T extends string>(props: Readonly<{ tabs: readonly CountTab<T>[]; value: T; counts: Partial<Record<T, number>>; onChange: (value: T) => void; label: string }>): JSX.Element {
  return <div class="count-tabs" role="tablist" aria-label={props.label}>{props.tabs.map((tab) => <button type="button" role="tab" aria-selected={props.value === tab.value} aria-label={`${tab.label} ${props.counts[tab.value] ?? 0}`} class="count-tab" onClick={() => props.onChange(tab.value)}>{tab.label}{" "}<span class="count-badge">{props.counts[tab.value] ?? 0}</span></button>)}</div>;
}
