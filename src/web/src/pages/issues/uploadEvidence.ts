import { ApiError, getToken } from "../../api/client";

export class EvidenceRejectedError extends Error {
  readonly detail: string;
  readonly ruleClasses: readonly string[];

  constructor(detail: string, ruleClasses: readonly string[]) {
    super(detail);
    this.name = "EvidenceRejectedError";
    this.detail = detail;
    this.ruleClasses = ruleClasses;
  }
}

type UnknownRecord = Readonly<Record<string, unknown>>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorRecord(body: unknown): UnknownRecord | undefined {
  if (!isRecord(body) || !isRecord(body.error)) return undefined;
  return body.error;
}

export async function uploadEvidence(file: Blob, issueId: string, filename: string): Promise<void> {
  const params = new URLSearchParams({ issue_id: issueId, filename });
  const headers = new Headers({ "Content-Type": file.type || "application/octet-stream" });
  const token = getToken();
  if (token !== null) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`/api/evidence?${params}`, { method: "PUT", headers, body: file });
  if (response.ok) return;

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  const error = errorRecord(body);
  const code = typeof error?.code === "string" ? error.code : "request_failed";
  const detail = typeof error?.detail === "string" ? error.detail : "The request could not be completed.";
  if (code === "evidence_rejected") {
    const rawClasses = error?.rule_classes;
    const ruleClasses = Array.isArray(rawClasses) ? rawClasses.filter((item): item is string => typeof item === "string") : [];
    throw new EvidenceRejectedError(detail, ruleClasses);
  }
  const errorId = typeof error?.error_id === "string" ? error.error_id : undefined;
  throw new ApiError(response.status, code, detail, errorId);
}
