export type ButtonVariant =
  "default" | "primary" | "danger" | "transparent" | "link";
export type TagVariant = "muted" | "info" | "danger" | "warning" | "success";
export type QueryKeySpec = Readonly<{
  key: string;
  values?: readonly string[];
  description: string;
}>;
export type QueryToken =
  | Readonly<{
      kind: "filter";
      key: string;
      value: string;
      negated: boolean;
      valid: boolean;
      reason?: string;
    }>
  | Readonly<{ kind: "text"; value: string }>;
export type MaskedSegment = Readonly<{ masked: boolean; text: string }>;
