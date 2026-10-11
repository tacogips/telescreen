import { parse } from "yaml";
import { notifyLabel } from "./sets";

export type WorkflowGraphNode = Readonly<{ id: string; type: string; notify?: string }>;
export type WorkflowGraphEdge = Readonly<{ from: string; to: string; kind: "next" | "branch" | "error" | "send_back" }>;
export type WorkflowGraphData = Readonly<{ nodes: WorkflowGraphNode[]; edges: WorkflowGraphEdge[] }>;

function record(value: unknown): Readonly<Record<string, unknown>> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Readonly<Record<string, unknown>> : null;
}

function target(value: unknown): string | null {
  if (typeof value === "string") return value;
  const item = record(value);
  return typeof item?.to === "string" ? item.to : null;
}

function addTarget(edges: WorkflowGraphEdge[], from: string, value: unknown, kind: WorkflowGraphEdge["kind"]): void {
  const to = target(value);
  if (to !== null) edges.push({ from, to, kind });
}

function edgesFor(step: Readonly<Record<string, unknown>>, id: string): WorkflowGraphEdge[] {
  const edges: WorkflowGraphEdge[] = [];
  addTarget(edges, id, step.next, "next");
  const outcomes = record(step.outcomes);
  if (outcomes !== null) for (const value of Object.values(outcomes)) addTarget(edges, id, value, "branch");
  const cases = Array.isArray(step.cases) ? step.cases : [];
  for (const item of cases) addTarget(edges, id, record(item)?.next, "branch");
  addTarget(edges, id, step.default, "branch");
  const branches = Array.isArray(step.branches) ? step.branches : [];
  for (const value of branches) addTarget(edges, id, value, "next");
  addTarget(edges, id, step.join, "next");
  addTarget(edges, id, step.on_error, "error");
  addTarget(edges, id, step.on_exhausted, "error");
  if (step.type === "send_back") addTarget(edges, id, step, "send_back");
  addTarget(edges, id, step.send_back, "send_back");
  return edges;
}

/** Parse a workflow source into nodes and known-target edges for preview. */
export function parseWorkflowGraph(text: string, format: "yaml" | "json"): WorkflowGraphData | null {
  try {
    const root = record(format === "json" ? JSON.parse(text) as unknown : parse(text) as unknown);
    if (root === null || !Array.isArray(root.steps)) return null;
    const records = root.steps.map(record);
    if (records.some((step) => step === null || typeof step.id !== "string")) return null;
    const steps = records.filter((step): step is Readonly<Record<string, unknown>> => step !== null && typeof step.id === "string");
    const nodes = steps.map((step): WorkflowGraphNode => {
      const node = { id: step.id as string, type: typeof step.type === "string" ? step.type : "step" };
      const notify = notifyLabel(step);
      return notify === null ? node : { ...node, notify };
    });
    const ids = new Set(nodes.map((node) => node.id));
    const edges = steps.flatMap((step) => edgesFor(step, step.id as string)).filter((edge) => ids.has(edge.to));
    return { nodes, edges };
  } catch {
    return null;
  }
}
