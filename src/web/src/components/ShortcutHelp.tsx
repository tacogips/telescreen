import type { JSX } from "solid-js";
export function ShortcutHelp(props: Readonly<{ open: boolean; onClose: () => void }>): JSX.Element {
  return props.open ? <div class="palette-backdrop" role="presentation" onClick={props.onClose}><section class="palette" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts" onClick={(event) => event.stopPropagation()}><h2>Keyboard shortcuts</h2><p><kbd>⌘ K</kbd> Search issues and pages</p><p><kbd>B</kbd> Toggle navigation</p><p><kbd>/</kbd> Open assistant</p><button type="button" onClick={props.onClose}>Close</button></section></div> : <></>;
}
