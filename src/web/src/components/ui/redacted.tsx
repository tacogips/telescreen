import type { JSX } from "solid-js";
import type { MaskedSegment } from "./types";
const maskedPattern =
  /(\*{3,}(?:\[\d+\])?|\[REDACTED[^\]]*\]|\[ADDRESS REDACTED\]|\*{4} \*{4} \*{4} \d{4})/gi;
export function splitMasked(text: string): MaskedSegment[] {
  const segments: MaskedSegment[] = [];
  let offset = 0;
  for (const match of text.matchAll(maskedPattern)) {
    const index = match.index ?? 0;
    if (index > offset)
      segments.push({ masked: false, text: text.slice(offset, index) });
    segments.push({ masked: true, text: match[0] });
    offset = index + match[0].length;
  }
  if (offset < text.length || segments.length === 0)
    segments.push({ masked: false, text: text.slice(offset) });
  return segments;
}
export function Redacted(
  props: Readonly<{ text: string; ruleClass?: string }>,
): JSX.Element {
  return (
    <span class="redacted">
      {splitMasked(props.text).map((part) =>
        part.masked ? (
          <span
            class="redacted-mask"
            title={`Masked (rule: ${props.ruleClass ?? "sensitive data"})`}
          >
            {part.text}
          </span>
        ) : (
          part.text
        ),
      )}
    </span>
  );
}
