import type { JSX } from "solid-js";
export function ProgressMarker(
  props: Readonly<{ step: 0 | 1 | 2 | 3 | 4 }>,
): JSX.Element {
  return (
    <span class="progress-marker" aria-label={`Step ${props.step} of 4`}>
      {props.step}/4
    </span>
  );
}
