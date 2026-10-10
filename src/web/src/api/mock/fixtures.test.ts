import { describe, expect, it } from "vitest";
import { eventFixtures, historyFixtures, issueFixtures, maskedExamples } from "./fixtures/issues";
import { auditFixtures, collectorFixtures, escalationFixtures, evidenceFixtures, mapJobFixtures, policeFixtures, runDetailFixture, runFixtures, searchFixtures, taskFixtures } from "./fixtures/operations";
import { memberFixtures, teamFixtures } from "./fixtures/directory";
import { coverageResourceFixtures, exceptionFixtures, occurrenceFixtures, policyFixtures, policyVersionFixtures, v11Issues } from "./fixtures/v11";

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
    expect(patterns.some((pattern) => pattern.test(serializedFixtures))).toBe(false);
    expect([...serializedFixtures.matchAll(/\b\d{13,19}\b/g)].map(([value]) => value).every(luhnValid)).toBe(true);
    expect(serializedFixtures).not.toMatch(/\d{8,}/);
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
