import type { JSX } from "solid-js";

export function LoadMore(props: Readonly<{ onMore: () => void; hasMore: boolean }>): JSX.Element {
  return <button type="button" class="button-secondary" onClick={props.onMore} disabled={!props.hasMore}>Load more</button>;
}
