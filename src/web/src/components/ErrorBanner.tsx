import type { JSX } from "solid-js";
import { ApiError } from "../api/client";

export function ErrorBanner(props: Readonly<{ error: ApiError | null }>): JSX.Element {
  return props.error === null ? <></> : <div class="error-banner" role="alert"><strong>{props.error.code}</strong><span>{props.error.detail}</span></div>;
}
