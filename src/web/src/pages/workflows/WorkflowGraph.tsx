import { Bot, Check, Clock3, GitBranch, Shield, UserRound, Workflow } from "lucide-solid";
import { For, Show, type Component, type JSX } from "solid-js";
import type { WorkflowGraphData, WorkflowGraphEdge } from "./graph";
import "./workflow-graph.css";

const icons: Readonly<Record<string, Component<{ size?: number }>>> = {
  detect: Shield,
  ai_agent: Bot,
  condition: GitBranch,
  switch: GitBranch,
  human_task: UserRound,
  wait: Clock3,
  timer: Clock3,
  police_verify: Shield,
  end: Check,
};

type PositionedNode = Readonly<{ id: string; type: string; notify?: string; x: number; y: number; rank: number }>;

function layout(graph: WorkflowGraphData): PositionedNode[] {
  const ranks = new Map<string, number>();
  const first = graph.nodes[0];
  if (first !== undefined) ranks.set(first.id, 0);
  const queue = first === undefined ? [] : [first.id];
  while (queue.length > 0) {
    const id = queue.shift();
    if (id === undefined) continue;
    const rank = ranks.get(id) ?? 0;
    for (const edge of graph.edges.filter((candidate) => candidate.from === id && candidate.kind !== "send_back")) {
      if (!ranks.has(edge.to)) { ranks.set(edge.to, rank + 1); queue.push(edge.to); }
    }
  }
  for (const node of graph.nodes) if (!ranks.has(node.id)) ranks.set(node.id, 0);
  const grouped = new Map<number, string[]>();
  for (const node of graph.nodes) {
    const rank = ranks.get(node.id) ?? 0;
    grouped.set(rank, [...(grouped.get(rank) ?? []), node.id]);
  }
  return graph.nodes.map((node) => {
    const rank = ranks.get(node.id) ?? 0;
    const row = grouped.get(rank) ?? [];
    return { ...node, rank, x: 36 + rank * 210, y: 34 + row.indexOf(node.id) * 92 };
  });
}

function edgePath(edge: WorkflowGraphEdge, nodes: ReadonlyMap<string, PositionedNode>): string {
  const from = nodes.get(edge.from);
  const to = nodes.get(edge.to);
  if (from === undefined || to === undefined) return "";
  if (edge.kind === "send_back") return "M " + from.x + " " + (from.y + 20) + " C " + (from.x - 40) + " " + (from.y + 82) + ", " + (to.x - 40) + " " + (to.y + 82) + ", " + to.x + " " + (to.y + 20);
  return "M " + (from.x + 72) + " " + (from.y + 20) + " C " + (from.x + 116) + " " + (from.y + 20) + ", " + (to.x - 116) + " " + (to.y + 20) + ", " + to.x + " " + (to.y + 20);
}

/** Render an accessible, read-only workflow graph preview. */
export function WorkflowGraph(props: Readonly<{ graph: WorkflowGraphData | null }>): JSX.Element {
  const positions = (): PositionedNode[] => props.graph === null ? [] : layout(props.graph);
  const nodeMap = (): ReadonlyMap<string, PositionedNode> => new Map(positions().map((node) => [node.id, node]));
  const width = (): number => Math.max(540, ...positions().map((node) => node.x + 170));
  const height = (): number => Math.max(180, ...positions().map((node) => node.y + 78));
  return <section class="workflow-graph-panel" aria-label="Workflow graph preview">
    <Show when={props.graph !== null} fallback={<p>Fix validation errors to preview</p>}>
      <svg class="workflow-graph" role="img" aria-label="Workflow graph preview" viewBox={"0 0 " + width() + " " + height()}>
        <defs><marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" /></marker></defs>
        <For each={props.graph?.edges ?? []}>{(edge) => <path class={"workflow-edge " + edge.kind} d={edgePath(edge, nodeMap())} marker-end="url(#workflow-arrow)" />}</For>
        <For each={positions()}>{(node) => {
          const Icon = icons[node.type] ?? Workflow;
          return <g class="workflow-node" transform={"translate(" + node.x + ", " + node.y + ")"}>
            <rect width="144" height={node.notify === undefined ? 42 : 58} rx="8" />
            <foreignObject x="10" y="9" width="22" height="24"><Icon size={18} /></foreignObject>
            <text x="40" y="18" class="workflow-node-type">{node.type}</text>
            <text x="40" y="33" class="workflow-node-id">{node.id}</text>
            <Show when={node.notify}>{(label) => <text x="40" y="50" class="workflow-node-notify">{label()}</text>}</Show>
          </g>;
        }}</For>
      </svg>
    </Show>
  </section>;
}
