import type { AlertFire, AlertRule } from "../../api/types";

export type RuleForm = Readonly<{
  name: string;
  trigger: string;
  threshold: number | null;
  window_minutes: number | null;
  filters: Readonly<{ min_severity: string; priorities: readonly string[]; sources: readonly string[]; rule_classes: readonly string[]; exposures: readonly string[]; owner_teams: readonly string[]; detectors: readonly string[] }>;
  actions: Readonly<{ targets: readonly Readonly<{ recipient: string; channels: readonly string[]; template: string | null }>[]; start_workflow: string | null }>;
  action_interval_minutes: number;
  enabled: boolean;
}>;

export type RuleTrigger = string | Readonly<{ kind: string; threshold: number | null; window_minutes: number | null }>;
export type RichAlertRule = Omit<AlertRule, "trigger" | "query"> & Readonly<{
  trigger: RuleTrigger;
  query?: string;
  filters?: Readonly<{ min_severity: string | null; priorities: readonly string[]; sources: readonly string[]; rule_classes: readonly string[]; exposures: readonly string[]; owner_teams: readonly string[]; detectors: readonly string[] }>;
  actions?: Readonly<{ targets: readonly Readonly<{ recipients: readonly string[]; channels: readonly string[]; template: string | null }>[]; start_workflow: string | null }>;
  last_fired_at?: string | null;
  fires_24h?: number;
  status?: string;
}>;
export type RichAlertFire = Omit<AlertFire, "at"> & Readonly<{ at?: string; fired_at?: string; trigger?: string; trigger_kind?: string; notification_count?: number; run_id?: string | null }>;

export const emptyRuleForm = (): RuleForm => ({
  name: "", trigger: "issue_created", threshold: null, window_minutes: null,
  filters: { min_severity: "", priorities: [], sources: [], rule_classes: [], exposures: [], owner_teams: [], detectors: [] },
  actions: { targets: [{ recipient: "", channels: ["email"], template: null }], start_workflow: null },
  action_interval_minutes: 60, enabled: true,
});

export function formFromRule(rule: RichAlertRule): RuleForm {
  const base = emptyRuleForm();
  const trigger = typeof rule.trigger === "string" ? rule.trigger : rule.trigger.kind;
  return {
    ...base,
    name: rule.name,
    trigger,
    threshold: typeof rule.trigger === "string" ? null : rule.trigger.threshold,
    window_minutes: typeof rule.trigger === "string" ? null : rule.trigger.window_minutes,
    filters: rule.filters ? { ...rule.filters, min_severity: rule.filters.min_severity ?? "" } : base.filters,
    actions: rule.actions ? { targets: rule.actions.targets.map((target) => ({ recipient: target.recipients.join(", "), channels: target.channels, template: target.template })), start_workflow: rule.actions.start_workflow } : base.actions,
    action_interval_minutes: rule.action_interval_minutes,
    enabled: rule.enabled,
  };
}

export function splitValues(value: string): readonly string[] {
  return value.split(",").map((item) => item.trim()).filter((item) => item.length > 0);
}

export type AlertValidation = Readonly<{ valid: boolean; errors: readonly Readonly<{ field: string; code: string }>[] }>;
export type AlertHistory = Readonly<{ items: readonly RichAlertFire[] }>;
