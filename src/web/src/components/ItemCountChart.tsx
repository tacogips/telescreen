import type { JSX } from "solid-js";

type Point = Readonly<{ at: string; item_count: number }>;
export function ItemCountChart(props: Readonly<{ points: readonly Point[]; label: string; height?: number }>): JSX.Element {
  const height = props.height ?? 72;
  if (props.points.length === 0) return <div class="item-count-chart-empty" role="img" aria-label={props.label}>No item count history</div>;
  const width = Math.max(1, props.points.length - 1) * 24;
  const max = Math.max(1, ...props.points.map((point) => point.item_count));
  const coordinates = props.points.map((point, index) => ({ x: index * 24, y: height - (point.item_count / max) * (height - 4) }));
  const path = coordinates.map((point, index) => index === 0 ? `M ${point.x} ${point.y}` : `H ${point.x} V ${point.y}`).join(" ");
  return <svg class="item-count-chart" role="img" aria-label={props.label} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"><path d={path} fill="none" stroke="var(--chart-matching)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>;
}
