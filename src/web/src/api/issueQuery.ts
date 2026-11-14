import type { Issue, QueryKeySpec, QueryToken, QueryValidation } from "./types";

export const ISSUE_QUERY_KEYS: readonly QueryKeySpec[] = [
  { key: "is", values: ["unresolved", "acknowledged", "in_progress", "regressed", "escalating", "archived", "resolved", "ignored", "allowed", "pending_verification"] },
  { key: "source", values: ["slack", "gdrive", "gdocs", "gsheets", "github", "custom", "police", "onedrive"] },
  { key: "severity", values: ["critical", "high", "medium"] },
  { key: "priority", values: ["high", "medium", "low"] },
  { key: "exposure", values: ["public", "anyone_with_link", "external_shared", "domain", "internal", "private", "unknown"] },
  { key: "rule", values: ["credential", "token", "private_key", "password", "payment_card", "national_id", "email", "phone", "address"] },
  { key: "assigned", values: ["me", "my_teams", "none" ] },
  { key: "has", values: ["assignee", "owner", "comments", "activity", "bookmark", "external_issue"] },
  { key: "first_seen", comparison: true }, { key: "last_seen", comparison: true },
  { key: "times_seen", comparison: true }, { key: "values", comparison: true },
  { key: "risk", comparison: true },
];

export function tokenizeIssueQuery(query: string): QueryToken[] {
  const tokens: QueryToken[] = [];
  const pattern = /!?[^\s]+/g;
  for (const match of query.matchAll(pattern)) {
    const raw = match[0];
    const negated = raw.startsWith("!");
    const text = negated ? raw.slice(1) : raw;
    const colon = text.indexOf(":");
    tokens.push({ key: colon < 0 ? null : text.slice(0, colon), value: colon < 0 ? text : text.slice(colon + 1), negated, start: match.index ?? 0, end: (match.index ?? 0) + raw.length });
  }
  return tokens;
}

export function validateIssueTokens(tokens: readonly QueryToken[]): QueryValidation[] {
  return tokens.map((token) => {
    if (token.key === null) return { valid: true, reason: null };
    const spec = ISSUE_QUERY_KEYS.find((item) => item.key === token.key);
    if (spec === undefined) return { valid: false, reason: "unknown_key" };
    if (spec.values !== undefined && !spec.values.includes(token.value)) return { valid: false, reason: "invalid_value" };
    if (spec.comparison && !/^(?:[<>=]{1,2})?-?\d+(?:d|h|m)?$/.test(token.value)) return { valid: false, reason: "invalid_comparison" };
    return { valid: true, reason: null };
  });
}

function compareDate(value: string, filter: string, now: number): boolean {
  const relative = /^-(\d+)([dhm])$/.exec(filter);
  const cutoff = relative === null ? Date.parse(filter) : now - Number(relative[1]) * (relative[2] === "d" ? 86_400_000 : relative[2] === "h" ? 3_600_000 : 60_000);
  return Number.isFinite(cutoff) && Date.parse(value) >= cutoff;
}

export function evaluateIssueQuery(issue: Issue, tokens: readonly QueryToken[], now: number): boolean {
  return tokens.every((token) => {
    let matched = true;
    if (token.key === null) { const shortId = /^TELE-(\d+)$/i.exec(token.value); matched = shortId === null ? `${issue.short_id} ${issue.display_title} ${issue.display_locator}`.toLowerCase().includes(token.value.toLowerCase()) : issue.number === Number(shortId[1]); }
    else if (token.key === "is") matched = token.value === "unresolved" ? ["unresolved", "acknowledged", "in_progress", "regressed", "escalating"].includes(issue.status) : issue.status === token.value;
    else if (token.key === "assigned") matched = token.value === "none" ? issue.assignee_member_id === null && issue.assignee_team_id === null : token.value === "me" ? issue.assignee_member_id === "mem-01" : true;
    else if (token.key === "has") matched = token.value === "assignee" ? issue.assignee_member_id !== null || issue.assignee_team_id !== null : true;
    else if (token.key === "source") matched = issue.source === token.value;
    else if (token.key === "severity") matched = issue.severity === token.value;
    else if (token.key === "priority") matched = (issue.priority ?? (issue.severity === "critical" || issue.severity === "high" ? "high" : "medium")) === token.value;
    else if (token.key === "exposure") matched = issue.exposure_visibility === token.value;
    else if (token.key === "rule") matched = issue.rule_class === token.value;
    else if (token.key === "first_seen" || token.key === "last_seen") matched = compareDate(issue[token.key], token.value.replace(/^>=?/, "-"), now);
    else if (token.key === "times_seen" || token.key === "values" || token.key === "risk") {
      const threshold = Number(token.value.replace(/^[<>=]+/, ""));
      const actual = token.key === "times_seen" ? issue.times_seen : token.key === "values" ? issue.distinct_fingerprints : issue.risk_score ?? 0;
      matched = token.value.startsWith(">") ? actual > threshold : token.value.startsWith("<") ? actual < threshold : actual === threshold;
    }
    return token.negated ? !matched : matched;
  });
}
