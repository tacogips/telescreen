import type { JSX } from "solid-js";
export function Avatar(props: Readonly<{ name: string; ref: string; size?: number }>): JSX.Element {
  const initials = (): string => props.name.trim().split(/\s+/).slice(0, 2).map((part) => part.charAt(0).toUpperCase()).join("");
  let hash = 0;
  for (const character of props.ref) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return <span class="avatar" title={props.name} aria-label={props.name} style={{ "--avatar-hue": String(hash % 360), width: `${props.size ?? 28}px`, height: `${props.size ?? 28}px` }}>{initials()}</span>;
}
