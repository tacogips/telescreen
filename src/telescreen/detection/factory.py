"""Configured detector and LLM client construction."""

from collections.abc import Sequence

import anthropic
import httpx

from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import ConfigError
from telescreen.core.secrets import EnvSource, SecretValue, resolve_secret
from telescreen.core.vocabulary import DetectorKind, ManagedLlmProvider
from telescreen.detection.composite import CompositeDetector
from telescreen.detection.harness import LlmClient
from telescreen.detection.local_llm import LocalLlmClient, LocalLlmDetector
from telescreen.detection.managed_llm import (
    ClaudeLlmClient,
    CodexLlmClient,
    ManagedLlmDetector,
)
from telescreen.detection.ml import MlDetector, TokenClassifier, load_transformers_classifier
from telescreen.detection.rule_detector import RuleDetector
from telescreen.detection.types import Detector


def build_managed_llm_client(
    settings: TelescreenSettings, env: EnvSource, http: httpx.AsyncClient
) -> LlmClient | None:
    """Construct the selected managed provider when its secret reference exists."""
    if settings.managed_llm_provider is ManagedLlmProvider.CLAUDE:
        if settings.anthropic_api_key_ref is None:
            return None
        secret: str = resolve_secret(settings.anthropic_api_key_ref, env).value
        return ClaudeLlmClient(anthropic.AsyncAnthropic(api_key=secret, http_client=http))
    if settings.openai_api_key_ref is None:
        return None
    key: SecretValue = resolve_secret(settings.openai_api_key_ref, env)
    return CodexLlmClient(http, settings.openai_base_url, key)


def build_local_llm_client(
    settings: TelescreenSettings, env: EnvSource, http: httpx.AsyncClient
) -> LocalLlmClient | None:
    """Construct the local provider when its URL and model are configured."""
    if settings.local_llm_base_url is None or settings.local_llm_model is None:
        return None
    key_ref: str | None = settings.local_llm_api_key_ref
    key: SecretValue | None = resolve_secret(key_ref, env) if key_ref is not None else None
    return LocalLlmClient(http, settings.local_llm_base_url, key)


def build_detector(
    settings: TelescreenSettings,
    env: EnvSource,
    http: httpx.AsyncClient,
    names: Sequence[DetectorKind],
    classifier: TokenClassifier | None = None,
) -> Detector:
    """Build configured stages in requested order, failing on missing providers."""
    if not names:
        raise ConfigError("at least one detector must be selected")
    stages: list[Detector] = []
    for name in names:
        if name is DetectorKind.RULE:
            stages.append(RuleDetector())
        elif name is DetectorKind.MANAGED_LLM:
            client: LlmClient | None = build_managed_llm_client(settings, env, http)
            if client is None:
                raise ConfigError("managed LLM detector requires a configured provider key")
            stages.append(
                ManagedLlmDetector(
                    client,
                    settings.managed_llm_model,
                    settings.llm_batch_max_chars,
                    settings.llm_batch_max_items,
                    settings.llm_concurrency,
                )
            )
        elif name is DetectorKind.LOCAL_LLM:
            client = build_local_llm_client(settings, env, http)
            if client is None or settings.local_llm_model is None:
                raise ConfigError("local LLM detector requires a base URL and model")
            stages.append(
                LocalLlmDetector(
                    client,
                    settings.local_llm_model,
                    settings.llm_batch_max_chars,
                    settings.llm_batch_max_items,
                    settings.llm_concurrency,
                )
            )
        elif name is DetectorKind.ML:
            if settings.ml_model is None:
                raise ConfigError("ML detector requires a model")
            stages.append(MlDetector(classifier or load_transformers_classifier(settings.ml_model)))
        else:
            raise ConfigError("unsupported detector")
    if len(stages) == 1 and stages[0].kind is DetectorKind.RULE:
        return stages[0]
    return CompositeDetector(tuple(stages), settings.detector_mode)
