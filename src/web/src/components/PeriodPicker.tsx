import type { JSX } from "solid-js";
export type Period = "all" | "24h" | "7d" | "14d" | "30d" | "custom";
export function PeriodPicker(props: Readonly<{ value: Period; onChange: (value: Period, start?: string, end?: string) => void }>): JSX.Element {
  return <div class="period-picker" aria-label="Time period"><label>Period <select aria-label="Period" value={props.value} onChange={(event) => props.onChange(event.currentTarget.value as Period)}><option value="all">All time</option><option value="24h">24 hours</option><option value="7d">7 days</option><option value="14d">14 days</option><option value="30d">30 days</option><option value="custom">Custom</option></select></label>{props.value === "custom" && <><input aria-label="Start date" type="date" onChange={(event) => props.onChange("custom", event.currentTarget.value)}/><input aria-label="End date" type="date" onChange={(event) => props.onChange("custom", undefined, event.currentTarget.value)}/></>}</div>;
}
