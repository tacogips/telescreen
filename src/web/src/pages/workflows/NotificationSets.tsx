import { For, Show, createMemo, type JSX } from "solid-js";
import { FoldSection, Tag } from "../../components/ui";
import { parseNotificationSets, type NotificationSetView } from "./sets";

function SetCard(props: Readonly<{ set: NotificationSetView }>): JSX.Element {
  return <article class="notification-set-card" aria-label={`Notification set ${props.set.name}`}>
    <h3>{props.set.name}</h3>
    <ul class="notification-targets"><For each={props.set.targets}>{(target, index) => <li>
      <span class="muted">Target {index()}</span>
      <span class="recipient-chips"><For each={target.recipients}>{(recipient) => <code class="recipient-chip">{recipient}</code>}</For></span>
      <span class="channel-tags"><For each={target.channels}>{(channel) => <Tag variant="info">{channel}</Tag>}</For></span>
      <span class="muted">{target.template === null ? "Default template" : `Template ${target.template}`}</span>
    </li>}</For></ul>
  </article>;
}

/** "Notification sets" fold section of the workflow definition view; v1 sources render nothing. */
export function NotificationSets(props: Readonly<{ source: string; format: "yaml" | "json" }>): JSX.Element {
  const result = createMemo(() => parseNotificationSets(props.source, props.format));
  const sets = (): readonly NotificationSetView[] | null => { const current = result(); return current.kind === "sets" ? current.sets : null; };
  return <Show when={result().kind !== "none"}>
    <FoldSection id="workflow-notification-sets" title="Notification sets">
      <Show when={sets()} fallback={<p class="muted">Unable to read notification sets</p>}>{(items) =>
        <Show when={items().length > 0} fallback={<p class="muted">No notification sets declared.</p>}><div class="notification-set-cards"><For each={items()}>{(item) => <SetCard set={item} />}</For></div></Show>}
      </Show>
    </FoldSection>
  </Show>;
}
