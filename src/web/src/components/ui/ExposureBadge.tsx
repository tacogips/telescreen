import type { JSX } from "solid-js";
import type { Visibility } from "../../api/types";
import { Tag } from "./Tag";

const labels: Readonly<Record<Visibility, string>> = {
  public: "Public",
  anyone_with_link: "Link sharing",
  external_shared: "Shared externally",
  domain: "Domain",
  internal: "Internal",
  private: "Private",
  unknown: "Unknown exposure",
};

export function ExposureBadge(props: Readonly<{ visibility: Visibility; externalPrincipalCount?: number; compact?: boolean }>): JSX.Element | null {
  if (props.compact && ["internal", "private", "unknown"].includes(props.visibility)) return null;
  const label = props.visibility === "external_shared" && (props.externalPrincipalCount ?? 0) > 0
    ? `${labels[props.visibility]} (${props.externalPrincipalCount})`
    : labels[props.visibility];
  return <Tag variant={props.visibility === "public" || props.visibility === "anyone_with_link" ? "danger" : props.visibility === "external_shared" ? "warning" : "muted"}>{label}</Tag>;
}
