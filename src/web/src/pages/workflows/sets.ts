import { parse } from "yaml";

export type NotificationTargetView = Readonly<{ recipients: readonly string[]; channels: readonly string[]; template: string | null }>;
export type NotificationSetView = Readonly<{ name: string; targets: readonly NotificationTargetView[] }>;
export type NotificationSetsResult =
  | Readonly<{ kind: "none" }>
  | Readonly<{ kind: "error" }>
  | Readonly<{ kind: "sets"; sets: readonly NotificationSetView[] }>;
type UnknownRecord = Readonly<Record<string, unknown>>;

const V2_API_VERSION = "telescreen.workflow/v2";
const NONE: NotificationSetsResult = { kind: "none" };
const ERROR: NotificationSetsResult = { kind: "error" };

function record(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : null;
}
function strings(value: unknown): readonly string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? value as readonly string[] : null;
}
function target(value: unknown): NotificationTargetView | null {
  const item = record(value);
  const recipients = strings(item?.recipients);
  const channels = strings(item?.channels);
  if (item === null || recipients === null || channels === null) return null;
  return { recipients, channels, template: typeof item.template === "string" ? item.template : null };
}
function notificationSet(value: unknown): NotificationSetView | null {
  const item = record(value);
  if (item === null || typeof item.name !== "string" || !Array.isArray(item.targets)) return null;
  const targets = item.targets.map(target);
  return targets.every((entry) => entry !== null) ? { name: item.name, targets: targets as NotificationTargetView[] } : null;
}

/** Read the v2 `notification_sets` of a workflow source. Never throws; v1 documents give `none`. */
export function parseNotificationSets(text: string, format: "yaml" | "json"): NotificationSetsResult {
  if (text.trim().length === 0) return NONE;
  let root: UnknownRecord | null;
  try { root = record(format === "json" ? JSON.parse(text) as unknown : parse(text) as unknown); }
  catch { return ERROR; }
  if (root === null) return NONE;
  const declared = root.notification_sets;
  if (declared === undefined || declared === null) return root.apiVersion === V2_API_VERSION ? { kind: "sets", sets: [] } : NONE;
  if (!Array.isArray(declared)) return ERROR;
  const sets = declared.map(notificationSet);
  return sets.every((entry) => entry !== null) ? { kind: "sets", sets: sets as NotificationSetView[] } : ERROR;
}

/** Graph label for a step's notification reference: `set: <name>`, "inline targets", or null. */
export function notifyLabel(step: UnknownRecord): string | null {
  const isNotify = step.type === "notify";
  const isTask = step.type === "human_task" || step.type === "evidence_upload";
  if (!isNotify && !isTask) return null;
  const setName = isNotify ? step.notification_set : step.notify_set;
  if (typeof setName === "string") return `set: ${setName}`;
  return Array.isArray(isNotify ? step.targets : step.notify_targets) ? "inline targets" : null;
}
