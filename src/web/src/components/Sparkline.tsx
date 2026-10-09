import type { JSX } from "solid-js";
import { MiniBarChart } from "./ui";
export function Sparkline(props: Readonly<{ values: number[]; label?: string }>): JSX.Element {
  return <MiniBarChart values={props.values} label={props.label ?? "Recent activity"} height={20}/>;
}
