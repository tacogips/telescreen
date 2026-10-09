import type { JSX } from "solid-js";
export function Table(props: Readonly<{ children: JSX.Element }>): JSX.Element {
  return (
    <div class="table-scroll">
      <table class="ui-table">{props.children}</table>
    </div>
  );
}
