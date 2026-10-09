import { describe, expect, it } from "vitest";
import { parseWorkflowGraph } from "./graph";

describe("parseWorkflowGraph", () => {
  it("parses YAML next, branch and send-back edges", () => {
    const graph = parseWorkflowGraph("steps:\n  - id: a\n    type: detect\n    next: b\n  - id: b\n    type: condition\n    cases:\n      - next: c\n    default: d\n  - id: c\n    type: end\n  - id: d\n    type: send_back\n    to: a", "yaml");
    expect(graph?.nodes).toHaveLength(4);
    expect(graph?.edges).toEqual([
      { from: "a", to: "b", kind: "next" },
      { from: "b", to: "c", kind: "branch" },
      { from: "b", to: "d", kind: "branch" },
      { from: "d", to: "a", kind: "send_back" },
    ]);
  });

  it("accepts JSON and ignores targets missing from the workflow", () => {
    const graph = parseWorkflowGraph(JSON.stringify({ steps: [{ id: "a", type: "wait", next: "missing" }] }), "json");
    expect(graph).toEqual({ nodes: [{ id: "a", type: "wait" }], edges: [] });
  });

  it("returns null for invalid source or a missing steps list", () => {
    expect(parseWorkflowGraph("steps: [", "yaml")).toBeNull();
    expect(parseWorkflowGraph("{\"name\":\"empty\"}", "json")).toBeNull();
  });
});
