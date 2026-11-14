"""Workflow executor for schema-constrained, redacted agent calls."""

from dataclasses import dataclass

from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonKind,
    JsonValue,
    dump_json,
    json_array,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.vocabulary import ErrorCode, StepType
from telescreen.detection.harness import LlmClient, LlmJsonRequest
from telescreen.masking.redact import Redactor
from telescreen.ports.workflow import StepSignal
from telescreen.workflow.dsl.model import AgentProvider, AiAgentStep, OutputField, OutputType
from telescreen.workflow.dsl.templates import render
from telescreen.workflow.step_api import StepContext, StepOutcome


@dataclass(frozen=True)
class AiAgentExecutor:
    """Call a configured LLM and persist only typed, redacted output."""

    managed: LlmClient | None
    local: LlmClient | None
    managed_model: str | None
    local_model: str | None
    redactor: Redactor
    step_type: StepType = StepType.AI_AGENT

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Run up to three schema-validated completion attempts."""
        step: AiAgentStep = _ai_step(ctx)
        client: LlmClient | None = (
            self.managed if step.provider is AgentProvider.MANAGED else self.local
        )
        if client is None:
            return StepOutcome.failed(ErrorCode.LLM_UNAVAILABLE, retryable=False)
        model: str = _model_for(step, self.managed_model, self.local_model)
        prompt: str = self.redactor.redact(render(step.prompt, ctx.context)).text
        request: LlmJsonRequest = LlmJsonRequest(
            model=model,
            system="Return one JSON object matching the schema; do not include extra fields.",
            user=prompt,
            json_schema=_output_schema(step.output),
            max_tokens=2048,
        )
        for _ in range(3):
            try:
                response: str = await client.complete_json(request)
            except Exception:
                return StepOutcome.failed(ErrorCode.PROVIDER_ERROR, retryable=True)
            try:
                value: JsonValue = parse_json(response)
                output: JsonValue | None = _validated_output(value, step.output, self.redactor)
            except TelescreenError:
                output = None
            if output is not None:
                return StepOutcome.completed(output, step.next)
        return StepOutcome.failed(ErrorCode.LLM_OUTPUT_INVALID, retryable=False)

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Agent steps do not wait for signals."""
        del ctx, signal
        return StepOutcome.failed(ErrorCode.INVALID_TRANSITION, retryable=False)


def _ai_step(ctx: StepContext) -> AiAgentStep:
    """Return the typed AI step from a validated workflow context."""
    if not isinstance(ctx.step, AiAgentStep):
        raise TypeError("executor step type mismatch")
    return ctx.step


def _model_for(step: AiAgentStep, managed_model: str | None, local_model: str | None) -> str:
    """Choose the step model or the selected provider's configured default."""
    if step.model is not None:
        return step.model
    if step.provider is AgentProvider.MANAGED:
        return managed_model or "claude-sonnet-5-5"
    return local_model or "local"


def _output_schema(fields: tuple[OutputField, ...]) -> str:
    """Build a strict JSON schema from the workflow's declared output fields."""
    properties: list[tuple[str, JsonValue]] = []
    for field in fields:
        if field.type is OutputType.ENUM:
            property_value: JsonValue = json_object(
                (
                    ("type", json_str("string")),
                    ("enum", json_array(tuple(json_str(item) for item in field.values))),
                )
            )
        else:
            property_value = json_object((("type", json_str(field.type.value)),))
        properties.append((field.name, property_value))
    return dump_json(
        json_object(
            (
                ("type", json_str("object")),
                ("properties", json_object(properties)),
                ("required", json_array(tuple(json_str(field.name) for field in fields))),
                ("additionalProperties", JsonValue(JsonKind.BOOL, bool_value=False)),
            )
        )
    )


def _validated_output(
    value: JsonValue, fields: tuple[OutputField, ...], redactor: Redactor
) -> JsonValue | None:
    """Validate exact field names and types, then redact string values."""
    if value.kind is not JsonKind.OBJECT or set(value.member_names()) != {
        field.name for field in fields
    }:
        return None
    members: list[tuple[str, JsonValue]] = []
    for field in fields:
        item: JsonValue | None = value.get(field.name)
        if item is None or not _matches_type(item, field):
            return None
        if item.kind is JsonKind.STRING:
            members.append((field.name, json_str(redactor.redact(item.str_value).text)))
        else:
            members.append((field.name, item))
    return json_object(members)


def _matches_type(value: JsonValue, field: OutputField) -> bool:
    """Check a value against one declared output type without coercion."""
    if field.type is OutputType.STRING:
        return value.kind is JsonKind.STRING
    if field.type is OutputType.BOOLEAN:
        return value.kind is JsonKind.BOOL
    if field.type is OutputType.NUMBER:
        return value.kind in (JsonKind.INT, JsonKind.FLOAT)
    return value.kind is JsonKind.STRING and value.str_value in field.values
