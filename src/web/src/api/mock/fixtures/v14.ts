/** Masked demo fixtures for the v1.4 data inventory UI. */
import type { DataAsset, DataAssetDetail, DataEvent, DataField, DataItem } from "../../types";

const kinds = ["file", "table", "list", "channel", "object", "folder", "other"] as const;
const states = ["present", "deleted", "unknown"] as const;
const eventKinds = ["discovered", "registered", "items_changed", "values_changed", "deleted", "restored", "metadata_changed", "manual_change", "tracking_stopped", "tracking_resumed"] as const;
const fields: DataField[] = [
  { id: "data-field-01", key: "purpose", label: "Purpose", description: null, field_type: "single_select", options: [{ value: "operations", label: "Operations" }, { value: "analytics", label: "Analytics" }], required: true, position: 0, role: "purpose", archived: false, value_count: 18, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-10T00:00:00Z" },
  { id: "data-field-02", key: "owner", label: "Owner", description: null, field_type: "reference", options: [], required: true, position: 1, role: "owner", archived: false, value_count: 20, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-10T00:00:00Z" },
  { id: "data-field-03", key: "deletion_due", label: "Deletion due", description: null, field_type: "date", options: [], required: false, position: 2, role: "deletion_due", archived: false, value_count: 8, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-10T00:00:00Z" },
  { id: "data-field-04", key: "retention_class", label: "Retention class", description: null, field_type: "multi_select", options: [{ value: "standard", label: "Standard" }, { value: "short", label: "Short" }], required: false, position: 3, role: null, archived: false, value_count: 13, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-10T00:00:00Z" },
  { id: "data-field-05", key: "notes", label: "Notes", description: null, field_type: "long_text", options: [], required: false, position: 4, role: null, archived: true, value_count: 2, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-10T00:00:00Z" },
  { id: "data-field-06", key: "record_count", label: "Record count", description: null, field_type: "integer", options: [], required: false, position: 5, role: null, archived: false, value_count: 3, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-10T00:00:00Z" },
];
const assets: DataAsset[] = Array.from({ length: 24 }, (_, index): DataAsset => {
  const number = index + 1;
  const state = states[index % states.length]!;
  const itemCount = index % 3 === 0 ? null : 12 + index;
  const due = index % 5 === 0 ? "2026-10-01" : index % 4 === 0 ? "2026-11-30" : null;
  return {
    id: `data-asset-${String(number).padStart(2, "0")}`, short_id: `DATA-${number}`, number,
    name: `Example ${kinds[index % kinds.length]} ${number}`, source: ["gdrive", "github", "slack", "custom"][index % 4]!,
    display_locator: `example.test/data-${number}`, kind: kinds[index % kinds.length]!, origin: index % 2 === 0 ? "discovered" : "registered", state,
    item_count: state === "deleted" ? 0 : itemCount, item_trend: itemCount === null ? [] : Array.from({ length: 14 }, (_, day) => Math.max(0, itemCount - (13 - day) + (day % 3))),
    open_issue_count: index % 4, owner: index % 3 === 0 ? { ref: "member:alex", display_name: "Alex Morgan" } : index % 3 === 1 ? { ref: "team:security", display_name: "Security" } : null,
    deletion_due: due, overdue: due !== null && due < "2026-10-13" && state !== "deleted", incomplete: index < 4 || index === 8 || index === 10,
    first_seen_at: "2026-10-01T00:00:00Z", last_seen_at: "2026-10-12T12:00:00Z", deleted_at: state === "deleted" ? "2026-10-11T12:00:00Z" : null,
    collector_config_id: index % 2 === 0 ? `col-0${(index % 7) + 1}` : null,
  };
});
const details: DataAssetDetail[] = assets.map((asset) => ({
  ...asset, sweep_covered: asset.origin === "discovered", created_by: asset.origin === "registered" ? { kind: "member", ref: "member:alex", display_name: "Alex Morgan" } : null,
  value_count: asset.incomplete ? 1 : 3,
  fields: fields.filter((field) => !field.archived).map((field) => ({ key: field.key, label: field.label, field_type: field.field_type, required: field.required, role: field.role, options: field.options, value_json: field.key === "owner" ? JSON.stringify(asset.owner?.ref ?? "member:alex") : field.key === "purpose" ? '"operations"' : field.key === "deletion_due" ? asset.deletion_due === null ? null : JSON.stringify(asset.deletion_due) : field.key === "retention_class" ? '["standard"]' : field.key === "record_count" ? "12" : null })),
  missing_required: asset.incomplete ? ["purpose"] : [], open_issues: asset.open_issue_count > 0 ? [{ issue_id: "v13-iss-01", short_id: "TELE-1" }] : [],
}));
const events: DataEvent[] = eventKinds.map((kind, index) => ({
  id: `data-event-${String(index + 1).padStart(2, "0")}`, kind, occurred_at: `2026-10-${String(index + 1).padStart(2, "0")}T10:00:00Z`, recorded_at: `2026-10-${String(index + 1).padStart(2, "0")}T10:01:00Z`, actor: { kind: "member", ref: "member:alex", display_name: "Alex Morgan" },
  items_added: kind === "items_changed" ? 3 : null, items_removed: kind === "items_changed" ? 1 : null, item_count_after: kind === "items_changed" ? 12 : null, values_added: kind === "values_changed" ? 1 : null, values_removed: kind === "values_changed" ? 0 : null, value_count_after: kind === "values_changed" ? 2 : null, field_key: kind === "metadata_changed" ? "purpose" : null, old_value: kind === "metadata_changed" ? "Analytics" : null, new_value: kind === "metadata_changed" ? "Operations" : null, note: kind === "manual_change" ? "Masked manual adjustment" : null,
}));
const items: DataItem[] = Array.from({ length: 12 }, (_, index) => ({ id: `data-item-${String(index + 1).padStart(2, "0")}`, kind: index < 8 ? "item" : "value", key_prefix: `a${(index + 1).toString(16)}b3c4d5e6f7`, first_seen_at: "2026-10-02T00:00:00Z", removed_at: index === 2 || index === 9 ? "2026-10-12T00:00:00Z" : null }));

export const v14Assets = assets;
export const v14AssetDetails = details;
export const v14Fields = fields;
export const v14Events = events;
export const v14Items = items;
