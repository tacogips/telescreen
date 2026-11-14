import type { JSX } from "solid-js";
import { Tooltip } from "./Tooltip";

export function RiskPill(props: Readonly<{ score: number }>): JSX.Element {
  const score = Math.max(0, Math.min(100, Math.round(props.score)));
  return <Tooltip label="Ordinal risk 0-100: severity x exposure x asset x age"><span class="risk-pill" aria-label={`Risk score ${score}`}>{score}</span></Tooltip>;
}
