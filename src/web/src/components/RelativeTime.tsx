import type { JSX } from "solid-js";
function relativeLabel(date: Date): string {
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const absolute = Math.abs(seconds);
  const unit = absolute < 60 ? "second" : absolute < 3600 ? "minute" : absolute < 86400 ? "hour" : "day";
  const divisor = unit === "second" ? 1 : unit === "minute" ? 60 : unit === "hour" ? 3600 : 86400;
  return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(Math.round(seconds / divisor), unit);
}
export function RelativeTime(props: Readonly<{ iso: string }>): JSX.Element {
  const date = new Date(props.iso);
  const valid = Number.isFinite(date.getTime());
  return <time datetime={valid ? date.toISOString() : undefined} title={valid ? date.toLocaleString() : undefined}>{valid ? relativeLabel(date) : "Unknown time"}</time>;
}
