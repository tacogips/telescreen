import { useParams } from "@solidjs/router";
import { Match, Switch, type JSX } from "solid-js";
import { useAuth } from "../../auth/useAuth";
import { Unavailable } from "../../components/Unavailable";
import DataDetail from "./DataDetail";
import DataList from "./DataList";
import DataRegister from "./DataRegister";

/** Routes the Data area to its list, registration form, or asset detail. */
export default function DataPage(): JSX.Element {
  const params = useParams<{ id?: string }>();
  const auth = useAuth();
  return <Switch fallback={<DataList />}>
    <Match when={!auth.can("data.read")}><Unavailable label="Data" /></Match>
    <Match when={params.id === "new"}><DataRegister /></Match>
    <Match when={params.id !== undefined}><DataDetail id={params.id ?? ""} /></Match>
  </Switch>;
}
