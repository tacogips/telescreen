import { For, createSignal, type JSX } from "solid-js";
import type { TemplatePreview as TemplatePreviewData } from "../../api/types";
import { Tag, type TagVariant } from "../../components/ui";

type PreviewChannel = "email" | "slack";
const CHANNELS: readonly Readonly<{ key: PreviewChannel; label: string }>[] = [{ key: "email", label: "Email" }, { key: "slack", label: "Slack" }];

function verdictVariant(verdict: string): TagVariant {
  if (verdict === "allow") return "success";
  return verdict === "block" || verdict === "blocked" ? "danger" : "warning";
}

/** Rendered preview per channel, with the outbound DLP verdict of each rendering. */
export function TemplatePreview(props: Readonly<{ preview: TemplatePreviewData }>): JSX.Element {
  const [channel, setChannel] = createSignal<PreviewChannel>("email");
  const rendered = (): TemplatePreviewData[PreviewChannel] => props.preview[channel()];
  return <section class="template-preview" aria-label="Preview">
    <div role="tablist" aria-label="Preview channel" class="template-preview-tabs">
      <For each={CHANNELS}>{(item) => <button type="button" role="tab" aria-selected={channel() === item.key} class={channel() === item.key ? "active" : ""} onClick={() => setChannel(item.key)}>{item.label} <Tag variant={verdictVariant(props.preview[item.key].dlp_verdict)}>{"DLP " + props.preview[item.key].dlp_verdict}</Tag></button>}</For>
    </div>
    <div role="tabpanel" aria-label={channel() === "email" ? "Email preview" : "Slack preview"}>
      <p><strong>{rendered().subject}</strong></p>
      <pre class="template-preview-body">{rendered().body}</pre>
    </div>
  </section>;
}
