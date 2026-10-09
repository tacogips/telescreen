import {
  parseQuery,
  type QueryKeySpec,
  type QueryToken,
} from "../../components/ui";

export const ISSUE_STATUSES = [
  "unresolved",
  "acknowledged",
  "in_progress",
  "resolved",
  "ignored",
  "regressed",
] as const;

export const ISSUE_SOURCES = [
  "slack",
  "gdrive",
  "gdocs",
  "gsheets",
  "github",
  "custom",
  "police",
] as const;

export const ISSUE_RULE_CLASSES = [
  "credential",
  "token",
  "private_key",
  "password",
  "payment_card",
  "national_id",
  "email",
  "phone",
  "address",
] as const;

export const ISSUE_QUERY_KEYS: readonly QueryKeySpec[] = [
  { key: "is", values: ISSUE_STATUSES, description: "Issue status" },
  { key: "severity", values: ["high", "medium"], description: "Severity" },
  { key: "rule", values: ISSUE_RULE_CLASSES, description: "Rule class" },
  { key: "source", values: ISSUE_SOURCES, description: "Source" },
  {
    key: "assigned",
    description: "member:<handle> or team:<slug>",
  },
];

const valuesByKey = new Map<string, readonly string[]>([
  ["is", ISSUE_STATUSES],
  ["severity", ["high", "medium"]],
  ["rule", ISSUE_RULE_CLASSES],
  ["source", ISSUE_SOURCES],
]);

export function issueQueryToParams(
  tokens: readonly QueryToken[],
  sort: string,
): URLSearchParams {
  const params = new URLSearchParams();
  const text: string[] = [];
  for (const token of tokens) {
    if (token.kind === "text") {
      text.push(token.value);
      continue;
    }
    if (!token.valid || token.negated) continue;
    const values = valuesByKey.get(token.key);
    if (values !== undefined && values.includes(token.value)) {
      params.set(
        token.key === "is"
          ? "status"
          : token.key === "rule"
            ? "rule_class"
            : token.key,
        token.value,
      );
    } else if (
      token.key === "assigned" &&
      /^(member|team):[a-z0-9_-]+$/i.test(token.value)
    ) {
      params.set("assignee", token.value);
    }
  }
  const search = text.join(" ").trim();
  if (search !== "") params.set("q", search);
  params.set("sort", sort);
  return params;
}

export function issueQueryTokens(query: string): QueryToken[] {
  return parseQuery(query, ISSUE_QUERY_KEYS);
}
