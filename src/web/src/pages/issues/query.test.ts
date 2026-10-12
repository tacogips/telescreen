import { describe, expect, it } from "vitest";
import { ISSUE_QUERY_KEYS, issueQueryTokens, tokenizeIssueQuery, validateIssueTokens } from "./query";

describe("canonical stream query", () => {
  it("preserves negated tokens verbatim for server submission", () => {
    const query = "!source:slack";
    const token = issueQueryTokens(query)[0];
    expect(token).toMatchObject({ key: "source", value: "slack", negated: true });
    expect(query.slice(token?.start ?? 0, token?.end ?? 0)).toBe("!source:slack");
  });

  it("re-exports the complete canonical query key specification", () => {
    expect(ISSUE_QUERY_KEYS.map((item) => item.key)).toEqual(expect.arrayContaining([
      "is", "source", "severity", "priority", "exposure", "rule", "assigned", "has", "first_seen", "last_seen", "times_seen", "values", "risk",
    ]));
  });

  it("validates the canonical token model without filtering invalid input", () => {
    const tokens = tokenizeIssueQuery("is:unresolved !source:slack");
    expect(validateIssueTokens(tokens)).toEqual([{ valid: true, reason: null }, { valid: true, reason: null }]);
    expect(tokens).toHaveLength(2);
  });
});
