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

  it("maps v1.1 filters, shortcuts, critical severity, and risk sort", () => {
    const tokens = issueQueryTokens(
      "is:pending_verification is:public exposure:external_shared assigned:my_teams severity:critical",
    );
    const params = issueQueryToParams(tokens, "risk");
    expect(params.get("status")).toBe("pending_verification");
    expect(params.get("exposure")).toBe("external_shared");
    expect(params.get("assignee")).toBe("my_teams");
    expect(params.get("severity")).toBe("critical");
    expect(params.get("sort")).toBe("risk");
  });

  it("maps allowed and public shorthand independently", () => {
    const allowed = issueQueryToParams(issueQueryTokens("is:allowed"), "last_seen");
    const publicExposure = issueQueryToParams(issueQueryTokens("is:public"), "last_seen");
    expect(allowed.get("status")).toBe("allowed");
    expect(publicExposure.get("exposure")).toBe("public");
  });

  it("omits invalid exposure values", () => {
    const tokens = issueQueryTokens("exposure:secret");
    const params = issueQueryToParams(tokens, "first_seen");
    expect(tokens.some((token) => token.kind === "filter" && !token.valid)).toBe(true);
    expect(params.has("exposure")).toBe(false);
  });
});
