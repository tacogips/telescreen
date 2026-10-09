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
