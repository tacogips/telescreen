// Human-readable labels for collector sources and rule classes, shared by the shell
// navigation and the issue pages so raw identifiers are never shown to users.

const SOURCE_LABELS: Readonly<Record<string, string>> = {
  slack: "Slack",
  gdrive: "Google Drive",
  gdocs: "Google Docs",
  gsheets: "Google Sheets",
  github: "GitHub",
  custom: "Custom",
  police: "Police",
};

const RULE_LABELS: Readonly<Record<string, string>> = {
  credential: "Credential",
  token: "Token",
  private_key: "Private key",
  password: "Password",
  payment_card: "Payment card",
  national_id: "National ID",
  email: "Email",
  phone: "Phone",
  address: "Address",
};

export const SOURCE_KEYS: readonly string[] = Object.keys(SOURCE_LABELS);
export const RULE_KEYS: readonly string[] = Object.keys(RULE_LABELS);

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

export function ruleLabel(rule: string): string {
  return RULE_LABELS[rule] ?? rule.replaceAll("_", " ");
}

export function visibilityLabel(visibility: string): string {
  return ({ public: "Public", anyone_with_link: "Link sharing", external_shared: "Shared externally", domain: "Domain", internal: "Internal", private: "Private", unknown: "Unknown" } as Readonly<Record<string, string>>)[visibility] ?? "Unknown";
}

export function coverageLabel(state: string, reason?: string | null): string {
  if (reason === "permission_denied") return "Permission denied";
  if (reason === "scan_incomplete") return "Scan incomplete";
  return ({ scanned: "Scanned", partial: "Partial", unsupported: "Unsupported", fetch_failed: "Fetch failed" } as Readonly<Record<string, string>>)[state] ?? "Unknown";
}

export function exceptionDurationLabel(duration: string): string {
  return ({ "7d": "7 days", "30d": "30 days", "90d": "90 days", until_regressed: "Until regressed", forever: "Forever (admin)" } as Readonly<Record<string, string>>)[duration] ?? duration;
}

export function healthConditionLabel(condition: string): string {
  return ({ stale: "Stale", watch_expiring: "Watch expiring", failing_scans: "Failing scans", unscannable_public: "Unscannable public resource", police_silent: "Police worker silent" } as Readonly<Record<string, string>>)[condition] ?? condition;
}
