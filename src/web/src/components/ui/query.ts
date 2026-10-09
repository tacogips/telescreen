import type { QueryKeySpec, QueryToken } from "./types";
const forbidden = /[!*\[,><]/;
export function parseQuery(
  text: string,
  keys: readonly QueryKeySpec[],
): QueryToken[] {
  const seen = new Set<string>();
  return text
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((word): QueryToken => {
      const match = /^([^:]+):(.*)$/.exec(word);
      if (match === null) return { kind: "text", value: word };
      const key = match[1]!;
      const value = match[2]!;
      const spec = keys.find((candidate) => candidate.key === key);
      const reason =
        spec === undefined
          ? `Supported keys: ${keys.map((item) => item.key).join(", ")}`
          : forbidden.test(value)
            ? "Filter value contains unsupported syntax"
            : seen.has(key)
              ? "This filter key is already used"
              : undefined;
      seen.add(key);
      return reason === undefined
        ? { kind: "filter", key, value, negated: false, valid: true }
        : { kind: "filter", key, value, negated: false, valid: false, reason };
    });
}
export function serializeTokens(tokens: readonly QueryToken[]): string {
  return tokens
    .map((token) =>
      token.kind === "text"
        ? token.value
        : `${token.negated ? "!" : ""}${token.key}:${token.value}`,
    )
    .join(" ");
}
