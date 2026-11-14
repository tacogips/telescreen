import type { JSX } from "solid-js";
import { useParams } from "@solidjs/router";
import { IssueDetail } from "./IssueDetail";
import { IssueList } from "./IssueList";

export default function IssuesPage(): JSX.Element {
  const params = useParams<{ id?: string }>();
  return params.id === undefined ? (
    <IssueList />
  ) : (
    <IssueDetail id={params.id} />
  );
}
