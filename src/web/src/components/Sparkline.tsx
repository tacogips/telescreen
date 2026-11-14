import type { JSX } from "solid-js";

export function Sparkline(props: Readonly<{ values: number[]; label?: string }>): JSX.Element {
  const points = (): string => {
    if (props.values.length === 0) return "0,10 100,10";
    const max = Math.max(...props.values, 1);
    return props.values.map((value, index) => {
      const x = props.values.length === 1 ? 50 : (index / (props.values.length - 1)) * 100;
      const y = 19 - (value / max) * 18;
      return `${x},${y}`;
    }).join(" ");
  };
  return <svg class="sparkline" viewBox="0 0 100 20" role="img" aria-label={props.label ?? "Recent activity"}><polyline points={points()} /></svg>;
}
