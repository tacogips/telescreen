import { describe, expect, it } from "vitest";
import { eventFixtures, historyFixtures, issueFixtures, maskedExamples } from "./fixtures/issues";
import { auditFixtures, collectorFixtures, escalationFixtures, evidenceFixtures, mapJobFixtures, policeFixtures, runDetailFixture, runFixtures, searchFixtures, taskFixtures } from "./fixtures/operations";
import { memberFixtures, teamFixtures } from "./fixtures/directory";
import { coverageResourceFixtures, exceptionFixtures, occurrenceFixtures, policyFixtures, policyVersionFixtures, v11Issues } from "./fixtures/v11";
import { v13AlertFires, v13AlertRules, v13Integrations, v13Issues, v13OwnershipRules, v13Settings, v13Tokens } from "./fixtures/v13";
import { state } from "./state";

function luhnValid(value: string): boolean {
  const digits = [...value].map(Number);
  let sum = 0;
  for (let index = 0; index < digits.length; index += 1) {
    let digit = digits[digits.length - index - 1]!;
    if (index % 2 === 1) { digit *= 2; if (digit > 9) digit -= 9; }
    sum += digit;
  }
  return digits.length >= 13 && digits.length <= 19 && sum % 10 === 0;
}

const serializedFixtures = JSON.stringify({ issueFixtures, eventFixtures, historyFixtures, maskedExamples, auditFixtures, collectorFixtures, escalationFixtures, evidenceFixtures, mapJobFixtures, policeFixtures, runDetailFixture, runFixtures, searchFixtures, taskFixtures, memberFixtures, teamFixtures });
const serializedV11Fixtures = JSON.stringify({ v11Issues, occurrenceFixtures, exceptionFixtures, policyFixtures, policyVersionFixtures, coverageResourceFixtures });
const serializedV13Fixtures = JSON.stringify({ v13Issues, v13AlertFires, v13AlertRules, v13Integrations, v13OwnershipRules, v13Settings, v13Tokens });

describe("masked fixture corpus", () => {
  it("keeps the v1.1 fixtures free of file names", () => {
    expect(serializedV11Fixtures).not.toMatch(/\.(xlsx|docx|pdf|csv)\b/i);
  });
  it("contains no raw credential or personal-data patterns", () => {
    const patterns = [
      /AKIA[0-9A-Z]{16}/,
      /ghp_[A-Za-z0-9]{36}/,
      /xox[abp]-[A-Za-z0-9-]{10,}/,
      /AIza[0-9A-Za-z_-]{35}/,
      /BEGIN [A-Z ]*PRIVATE KEY-----\s*[A-Za-z0-9+/=]{20}/,
      /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/,
      /\b\d{13,19}\b/,
      /\d{12}/,
      /[A-Za-z0-9._%+-]+@(?!example\.(?:com|org|net)\b|[A-Za-z0-9.-]+\.invalid\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}/,
    ];
    expect(patterns.some((pattern) => pattern.test(`${serializedFixtures}${serializedV13Fixtures}`))).toBe(false);
    expect([...`${serializedFixtures}${serializedV13Fixtures}`.matchAll(/\b\d{13,19}\b/g)].map(([value]) => value).every(luhnValid)).toBe(true);
    expect(`${serializedFixtures}${serializedV13Fixtures}`).not.toMatch(/\d{8,}/);
  });

  it("seeds v1.3 with at least two issues in every status and all dimensions", () => {
    expect(v13Issues.length).toBeGreaterThanOrEqual(40);
    for (const status of ["unresolved", "acknowledged", "in_progress", "regressed", "escalating", "archived", "resolved", "ignored", "allowed", "pending_verification"]) {
      expect(v13Issues.filter((issue) => issue.status === status).length).toBeGreaterThanOrEqual(2);
    }
    expect(new Set(v13Issues.map((issue) => issue.source)).size).toBeGreaterThanOrEqual(7);
    expect(new Set(v13Issues.map((issue) => issue.severity)).size).toBe(3);
    expect(new Set(v13Issues.map((issue) => issue.priority)).size).toBe(3);
    expect(new Set(v13Issues.map((issue) => issue.exposure_visibility)).size).toBe(7);
    expect(v13Issues.filter((issue) => issue.merged_into_id === "v13-iss-01")).toHaveLength(2);
    expect(new Set(v13Issues.flatMap((issue) => issue.assignee?.ref ?? [])).size).toBeGreaterThanOrEqual(5);
    expect(new Set(v13Issues.map((issue) => issue.source))).toEqual(new Set(["slack", "gdrive", "gdocs", "gsheets", "github", "custom", "police", "onedrive"]));
    expect(state.comments).toHaveLength(1);
    expect(state.comments[0]?.display_text).toContain("@alice");
    expect(state.externalLinks).toHaveLength(1);
    expect(v13AlertRules).toHaveLength(3);
    expect(v13AlertFires).toHaveLength(3);
    expect(new Set(v13AlertFires.map((fire) => fire.outcome))).toEqual(new Set(["delivered", "suppressed_interval", "muted"]));
    expect(v13Settings).toHaveLength(6);
    expect(v13Tokens.length).toBeGreaterThanOrEqual(1);
    expect(v13Tokens.every((token) => token.preview.includes("••••"))).toBe(true);
    expect(v13OwnershipRules).toHaveLength(4);
    expect(v13Integrations.map((integration) => integration.kind)).toEqual(["slack", "email", "github_issues", "oidc"]);
    expect(v13Integrations.find((integration) => integration.kind === "github_issues")?.configured).toBe(false);
  });

  it("includes approved masked examples for every fixture kind", () => {
    for (const masked of ["ghp_****************[40]", "AKIA****************[20]", "********[24]", "**** **** **** 1111", "************", ["j***", "@example.com"].join(""), "***-****-**78", "[ADDRESS REDACTED]", "-----BEGIN PRIVATE KEY----- [REDACTED 1675 chars]"]) {
      expect(maskedExamples).toContain(masked);
      expect(serializedFixtures).toContain(masked);
    }
  });

  it("stores escalation tiers in the backend escalation_to_json document shape", () => {
    for (const policy of escalationFixtures) {
      const document = JSON.parse(policy.tiers_json) as { name: unknown; tiers: unknown };
      expect(document.name).toBe(policy.name);
      expect(Array.isArray(document.tiers)).toBe(true);
      for (const tier of document.tiers as Record<string, unknown>[]) {
        expect(Object.keys(tier).sort()).toEqual(["after", "assign_to", "channels", "notify"]);
        expect(tier.after).toMatch(/^PT\d+[HMS]$/);
        expect(tier.assign_to).toMatch(/^(member|team|email|slack):.+$/);
      }
    }
  });
});
