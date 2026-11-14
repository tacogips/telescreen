import { For, type JSX } from "solid-js";
export function SortSelect(
  props: Readonly<{
    value: string;
    options: readonly Readonly<{
      value: string;
      label: string;
      detail: string;
    }>[];
    onChange(value: string): void;
  }>,
): JSX.Element {
  const selected = ():
    Readonly<{ value: string; label: string; detail: string }> | undefined =>
    props.options.find((option) => option.value === props.value);
  return (
    <label class="sort-select">
      <select
        aria-label="Sort"
        value={props.value}
        onChange={(event) => props.onChange(event.currentTarget.value)}
      >
        <For each={props.options}>
          {(option) => <option value={option.value}>{option.label}</option>}
        </For>
      </select>
      <span class="sort-detail">{selected()?.detail ?? ""}</span>
    </label>
  );
}
