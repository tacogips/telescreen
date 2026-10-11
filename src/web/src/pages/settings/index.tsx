import { useParams } from "@solidjs/router";
import { Match, Switch, type JSX } from "solid-js";
import { GeneralSettings } from "./GeneralSettings";
import { TemplateDetail } from "./TemplateDetail";
import { TemplatesList } from "./TemplatesList";
import { UsersRoles } from "./UsersRoles";
import "./settings.css";

function decodeName(value: string): string {
  try { return decodeURIComponent(value); } catch { return value; }
}

/** Settings section switch: General, Notification templates and Users and roles. */
export default function SettingsPage(): JSX.Element {
  // The template name is the whole `:sub` segment; names such as `system.workflow_notify` keep their dots.
  const params = useParams<{ id?: string; sub?: string }>();
  return <Switch fallback={<GeneralSettings />}>
    <Match when={params.id === "templates" && params.sub !== undefined}><TemplateDetail name={decodeName(params.sub ?? "")} /></Match>
    <Match when={params.id === "templates"}><TemplatesList /></Match>
    <Match when={params.id === "users"}><UsersRoles /></Match>
  </Switch>;
}
