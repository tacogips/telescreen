import { describe, expect, it } from "vitest";
import { issueQueryToParams, issueQueryTokens } from "./query";

describe("issue query conversion", () => {
  it("maps supported filters and free text to API fields", () => {
    const tokens = issueQueryTokens("is:resolved rule:token foo");
    const params = issueQueryToParams(tokens, "last_seen");
    expect(params.toString()).toBe(
      "status=resolved&rule_class=token&q=foo&sort=last_seen",
    );
  });

  it("omits invalid filters and unsupported assignment aliases", () => {
    const tokens = issueQueryTokens("severity:[high, medium] assigned:me");
    const params = issueQueryToParams(tokens, "first_seen");
    expect(params.has("severity")).toBe(false);
    expect(params.has("assignee")).toBe(false);
    expect(params.get("sort")).toBe("first_seen");
  });
});
