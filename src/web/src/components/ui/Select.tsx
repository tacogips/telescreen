import type { JSX } from "solid-js";
export function Select(
  props: JSX.SelectHTMLAttributes<HTMLSelectElement>,
): JSX.Element {
  return (
    <select {...props} class={`ui-select ${props.class ?? ""}`}>
      {props.children}
    </select>
  );
}
