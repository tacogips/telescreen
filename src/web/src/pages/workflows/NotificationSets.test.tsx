import { cleanup, render, screen, within } from "@solidjs/testing-library";
import { MemoryRouter, Route } from "@solidjs/router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NotificationSets } from "./NotificationSets";
import { WorkflowEditor } from "./WorkflowEditor";
import { parseNotificationSets } from "./sets";
import { parseWorkflowGraph } from "./graph";

// Local fixtures: the shared mock fixtures contain no v2 workflow.
const V2_SOURCE = `apiVersion: telescreen.workflow/v2
name: secops-leak
notification_sets:
  - name: secops-alert
    targets:
      - {recipients: [team:secops, slack_channel:C0123ABCDE], channels: [slack]}
      - {recipients: [email:secops-leads@example.invalid], channels: [email], template: secops-leak-mail}
  - name: owners
    targets:
      - {recipients: [email:owners@example.invalid], channels: [email]}
steps:
  - {id: alert, type: notify, notification_set: secops-alert, template: secops-leak-alert, next: triage}
  - {id: triage, type: human_task, assignee: team:secops, notify_set: owners, next: tell}
  - {id: tell, type: notify, targets: [{recipients: [slack_channel:C0123ABCDE], channels: [slack]}], next: done}
  - {id: done, type: end}
`;
const V1_SOURCE = `apiVersion: telescreen.workflow/v1
name: incident
steps:
  - {id: alert, type: notify, to: team:secops, channel: slack, subject: Alert, body: Review the issue., next: done}
  - {id: done, type: end}
`;

afterEach(() => { cleanup(); vi.unstubAllGlobals(); globalThis.localStorage?.clear(); });

describe("NotificationSets", () => {
  it("shows one card per set of a v2 workflow", () => {
    render(() => <NotificationSets source={V2_SOURCE} format="yaml" />);
    const cards = screen.getAllByRole("article");
    expect(cards).toHaveLength(2);
    const alert = within(screen.getByRole("article", { name: "Notification set secops-alert" }));
    expect(alert.getAllByText("slack_channel:C0123ABCDE")).toHaveLength(1);
    expect(alert.getByText("email:secops-leads@example.invalid")).toBeInTheDocument();
    expect(alert.getByText("Template secops-leak-mail")).toBeInTheDocument();
    expect(alert.getByText("slack")).toBeInTheDocument();
  });

  it("shows nothing for a v1 workflow", () => {
    render(() => <NotificationSets source={V1_SOURCE} format="yaml" />);
    expect(screen.queryByText("Notification sets")).toBeNull();
    expect(parseNotificationSets(V1_SOURCE, "yaml")).toEqual({ kind: "none" });
  });

  it("shows the fallback text for invalid YAML without throwing", () => {
    render(() => <NotificationSets source={"notification_sets: [\n  - name: broken"} format="yaml" />);
    expect(screen.getByText("Unable to read notification sets")).toBeInTheDocument();
    expect(parseNotificationSets("{\"notification_sets\": 3}", "json")).toEqual({ kind: "error" });
  });

  it("labels notify and human task nodes with their set or inline targets", () => {
    const nodes = parseWorkflowGraph(V2_SOURCE, "yaml")?.nodes ?? [];
    expect(nodes.map((node) => node.notify)).toEqual(["set: secops-alert", "set: owners", "inline targets", undefined]);
    expect(parseWorkflowGraph(V1_SOURCE, "yaml")?.nodes.every((node) => node.notify === undefined)).toBe(true);
  });

  it("renders the sets panel and node labels in the workflow definition view", async () => {
    const version = { id: "wf-02", name: "secops-leak", version: 1, source_format: "yaml", source_text: V2_SOURCE, compiled_json: "{}", active: true };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/schema")) return Response.json({ type: "object" });
      if (url.endsWith("/versions/1")) return Response.json(version);
      return Response.json({ items: [{ id: "wf-02", name: "secops-leak", version: 1, active: true }] });
    }));
    render(() => <MemoryRouter><Route path="*" component={() => <WorkflowEditor name="secops-leak" />} /></MemoryRouter>);
    (await screen.findByRole("button", { name: "Open" })).click();
    expect(await screen.findByRole("button", { name: "Notification sets" })).toBeInTheDocument();
    expect(screen.getAllByRole("article")).toHaveLength(2);
    expect(screen.getByText("set: secops-alert")).toBeInTheDocument();
    expect(screen.getByText("inline targets")).toBeInTheDocument();
  });
});
