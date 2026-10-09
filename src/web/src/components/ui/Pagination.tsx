import type { JSX } from "solid-js";
import { Button } from "./Button";
export function Pagination(
  props: Readonly<{
    page: number;
    pages: number;
    onChange(page: number): void;
  }>,
): JSX.Element {
  return (
    <nav aria-label="Pagination">
      <Button
        size="sm"
        disabled={props.page <= 1}
        onClick={() => props.onChange(props.page - 1)}
      >
        Previous
      </Button>
      <span>
        {props.page} / {props.pages}
      </span>
      <Button
        size="sm"
        disabled={props.page >= props.pages}
        onClick={() => props.onChange(props.page + 1)}
      >
        Next
      </Button>
    </nav>
  );
}
