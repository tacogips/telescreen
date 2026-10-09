import type { JSX } from "solid-js";
export function MiniBarChart(
  props: Readonly<{
    values: readonly number[];
    total?: readonly number[];
    label: string;
    height?: number;
  }>,
): JSX.Element {
  const max = Math.max(1, ...props.values, ...(props.total ?? []));
  const height = props.height ?? 24;
  return (
    <svg
      role="img"
      aria-label={props.label}
      viewBox={`0 0 ${Math.max(1, props.values.length) * 8} ${height}`}
      class="mini-bar-chart"
    >
      {props.values.map((value, index) => (
        <>
          <rect
            x={index * 8}
            y="0"
            width="6"
            height={height}
            fill="var(--chart-total)"
          />
          <rect
            x={index * 8}
            y={height - (value / max) * height}
            width="6"
            height={(value / max) * height}
            fill="var(--chart-matching)"
          />
        </>
      ))}
    </svg>
  );
}
