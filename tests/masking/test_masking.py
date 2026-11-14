"""Behavior and leak-prevention tests for the masking boundary."""

import io
import logging
import sys
from collections.abc import Callable
from types import TracebackType
from typing import cast

import pytest

from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import DetectorKind, ErrorCode, RuleClass
from telescreen.detection import DetectionResult, RawFinding, RuleDetector, ScanItem, Span
from telescreen.detection.rules.catalog import PUBLIC_PREFIXES
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.index_guard import GuardVerdict, IndexGuard
from telescreen.masking.log_masking import RecordFactory, configure_logging, install_excepthook
from telescreen.masking.mask import mask_value
from telescreen.masking.redact import RedactionResult, Redactor
from telescreen.masking.safe_errors import SafeError, new_error_id, safe_error_for
from telescreen.masking.seal import Masker
from telescreen.ports.findings import MaskedFinding
from tests.support.samples import (
    Sample,
    all_samples,
    assert_no_leaks,
    credit_card,
    email_address,
    github_token,
    pem_private_key,
)


def _retention_parts(sample: Sample, masked: str) -> tuple[str, str]:
    """Remove policy-exempt context and display metadata before measuring retention."""
    if sample.rule_class in (RuleClass.CREDENTIAL, RuleClass.TOKEN):
        prefix: str = next(
            (
                candidate
                for candidate in sorted(PUBLIC_PREFIXES, key=len, reverse=True)
                if sample.value.startswith(candidate)
            ),
            "",
        )
        return sample.value[len(prefix) :], masked[len(prefix) :].split("[", 1)[0]
    if sample.rule_class is RuleClass.PRIVATE_KEY:
        header: str = sample.value.splitlines()[0]
        secret: str = sample.value[len(header) :]
        masked_secret: str = masked.partition(" [REDACTED ")[0][len(header) :]
        return secret, masked_secret
    if sample.rule_class is RuleClass.EMAIL:
        return sample.value.partition("@")[0], masked.partition("@")[0].replace("*", "")
    if sample.rule_class in (RuleClass.PAYMENT_CARD, RuleClass.PHONE):
        source_digits: str = "".join(character for character in sample.value if character.isdigit())
        masked_digits: str = "".join(character for character in masked if character.isdigit())
        return source_digits, masked_digits
    return sample.value, masked.split("[", 1)[0]


def _retained_source_characters(source: str, masked: str) -> int:
    """Count source characters preserved in order in the masked representation."""
    previous: list[int] = [0] * (len(masked) + 1)
    for source_character in source:
        current: list[int] = [0] * (len(masked) + 1)
        for index, masked_character in enumerate(masked, start=1):
            if source_character == masked_character:
                current[index] = previous[index - 1] + 1
            else:
                current[index] = max(previous[index], current[index - 1])
        previous = current
    return previous[-1]


def test_masking_invariant_and_redaction_cover_every_sample() -> None:
    """Every shared synthetic sample is masked, leak-free and idempotent."""
    redactor: Redactor = Redactor.default()
    for sample in all_samples():
        masked: str = mask_value(sample.value, sample.name, sample.rule_class)
        retention_parts: tuple[str, str] = _retention_parts(sample, masked)
        secret_length: int = len(retention_parts[0])
        retained: int = _retained_source_characters(retention_parts[0], retention_parts[1])
        assert retained <= 4
        assert retained <= secret_length * 0.25
        assert_no_leaks(masked, sample.name)
        assert redactor.redact(masked).hit_count == 0


def test_headerless_private_key_masks_every_secret_character() -> None:
    """Headerless private-key spans never keep key material."""
    single_line_key: str = "Z9" * 24
    multiline_key: str = single_line_key + "\n" + "X7" * 8
    for key in (single_line_key, multiline_key):
        masked: str = mask_value(key, "llm_private_key", RuleClass.PRIVATE_KEY)
        assert masked == f"[REDACTED PRIVATE KEY {len(key)} chars]"
        assert _retained_source_characters(key, masked) <= 4
        assert _retained_source_characters(key, masked) <= len(key) * 0.25
        for start in range(len(key) - 4):
            assert key[start : start + 5] not in masked
    pem: Sample = pem_private_key()
    pem_masked: str = mask_value(pem.value, pem.name, RuleClass.PRIVATE_KEY)
    assert pem_masked.startswith("-----BEGIN RSA PRIVATE KEY----- [REDACTED ")
    assert pem_masked.endswith(f"{len(pem.value)} chars]")
    assert mask_value("", "llm_private_key", RuleClass.PRIVATE_KEY) == (
        "[REDACTED PRIVATE KEY 0 chars]"
    )


def test_fingerprints_normalize_equivalent_values_and_hide_key() -> None:
    """Fingerprints are stable across specified normalization and keyed."""
    fingerprinter: Fingerprinter = Fingerprinter.from_secret(SecretValue("k" * 32))
    other: Fingerprinter = Fingerprinter.from_secret(SecretValue("q" * 32))
    card_sample: Sample = credit_card()
    card: str = card_sample.value
    spaced_card: str = f"{card[:4]} {card[4:8]} {card[8:12]} {card[12:]}"
    assert fingerprinter.fingerprint("credit_card", card, RuleClass.PAYMENT_CARD) == (
        fingerprinter.fingerprint("credit_card", spaced_card, RuleClass.PAYMENT_CARD)
    )
    assert fingerprinter.fingerprint("credit_card", card, RuleClass.PAYMENT_CARD) != (
        other.fingerprint("credit_card", card, RuleClass.PAYMENT_CARD)
    )
    assert len(fingerprinter.key_id) == 8
    assert "k" * 32 not in repr(fingerprinter)
    with pytest.raises(ConfigError):
        Fingerprinter.from_secret(SecretValue("short"))


def test_email_and_numeric_fingerprint_normalization() -> None:
    """NFKC digits and email case normalize before HMAC calculation."""
    fingerprinter: Fingerprinter = Fingerprinter.from_secret(SecretValue("z" * 32))
    assert fingerprinter.fingerprint("email", "A@EXAMPLE.INVALID", RuleClass.EMAIL) == (
        fingerprinter.fingerprint("email", "a@example.invalid", RuleClass.EMAIL)
    )
    assert fingerprinter.fingerprint("phone", "090-1234-5678", RuleClass.PHONE) == (
        fingerprinter.fingerprint("phone", "０９０１２３４５６７８", RuleClass.PHONE)
    )


def test_redactor_and_index_guard_never_return_raw_values() -> None:
    """Redaction masks content and the index guard reports metadata only."""
    redactor: Redactor = Redactor.default()
    sample: Sample = github_token()
    result: RedactionResult = redactor.redact(f"token={sample.value}")
    assert result.hit_count == 1
    assert result.rule_classes == (RuleClass.TOKEN,)
    assert_no_leaks(result.text, "redactor")
    verdict: GuardVerdict = IndexGuard(RuleDetector()).check(
        (("title", "ok"), ("body", sample.value))
    )
    assert verdict.accepted is False
    assert verdict.field_names == ("body",)
    assert verdict.rule_ids
    assert IndexGuard(RuleDetector()).check((("body", result.text),)).accepted is True
    masked_email: str = mask_value(email_address().value, "email", RuleClass.EMAIL)
    assert masked_email.replace("\u2060", "") == "s***@example.invalid"
    assert mask_value("john", "llm_email", RuleClass.EMAIL) == "********[4]"
    redactor_result: RedactionResult = Redactor.default().redact(masked_email)
    assert redactor_result.hit_count == 0
    assert IndexGuard(RuleDetector()).check((("body", masked_email),)).accepted is True
    raw_mask_shape: str = "s***@example.invalid"
    assert IndexGuard(RuleDetector()).check((("body", raw_mask_shape),)).accepted is False
    remasked: RedactionResult = Redactor.default().redact(raw_mask_shape)
    assert remasked.hit_count == 1
    assert remasked.text == masked_email


def test_seal_masks_multiple_findings_and_locator() -> None:
    """Sealed finding snippets and locations contain only safe representations."""
    card: Sample = credit_card()
    email: Sample = email_address()
    text: str = f"card={card.value} email={email.value}"
    item: ScanItem = ScanItem("item", "test", email.value, text)
    detector: RuleDetector = RuleDetector()
    raw_findings: list[RawFinding] = detector.scan_text(text)
    findings: list[MaskedFinding] = Masker(
        Fingerprinter.from_secret(SecretValue("f" * 32)), Redactor.default()
    ).seal(item, DetectionResult("item", tuple(raw_findings)))
    assert len(findings) == 2
    for finding in findings:
        assert len(finding.masked_snippet) <= 200
        assert_no_leaks(finding.masked_snippet, "sealed snippet")
        assert_no_leaks(finding.location, "sealed location")


def test_seal_masks_headerless_key_from_non_rule_detector() -> None:
    """A non-rule detector's headerless private key is masked before persistence."""
    key: str = "Z9" * 24
    text: str = f"PRIVATE_KEY={key}"
    start: int = len("PRIVATE_KEY=")
    finding: RawFinding = RawFinding(
        "llm_private_key",
        RuleClass.PRIVATE_KEY,
        Span(start, start + len(key)),
        0.9,
        DetectorKind.MANAGED_LLM,
    )
    item: ScanItem = ScanItem("item", "test", "resource", text)
    sealed: list[MaskedFinding] = Masker(
        Fingerprinter.from_secret(SecretValue("h" * 32)), Redactor.default()
    ).seal(item, DetectionResult("item", (finding,)))
    assert len(sealed) == 1
    snippet: str = sealed[0].masked_snippet
    assert key not in snippet
    assert f"[REDACTED PRIVATE KEY {len(key)} chars]" in snippet
    assert_no_leaks(snippet, "non-rule sealed private key")


def test_seal_masks_findings_crossing_snippet_boundaries() -> None:
    """A context window never exposes a partial raw value at its edge."""
    email: Sample = email_address()
    token: Sample = github_token()
    text: str = f"{email.value} {'x' * 20} {token.value}"
    item: ScanItem = ScanItem("item", "test", "resource", text)
    raw_findings: list[RawFinding] = RuleDetector().scan_text(text)
    findings: list[MaskedFinding] = Masker(
        Fingerprinter.from_secret(SecretValue("g" * 32)), Redactor.default()
    ).seal(item, DetectionResult("item", tuple(raw_findings)))
    email_finding: MaskedFinding = next(
        finding for finding in findings if finding.rule_class is RuleClass.EMAIL
    )
    assert token.value[4:12] not in email_finding.masked_snippet
    assert_no_leaks(email_finding.masked_snippet, "boundary-crossing snippet")


def test_logging_masks_arguments_and_exception_tracebacks() -> None:
    """Captured logs redact formatted arguments and exception traceback text."""
    root: logging.Logger = logging.getLogger()
    saved_handlers: list[logging.Handler] = root.handlers[:]
    saved_level: int = root.level
    saved_factory: RecordFactory = cast(RecordFactory, logging.getLogRecordFactory())
    saved_logger_class: type[logging.Logger] = logging.getLoggerClass()
    custom_logger_class: type[logging.Logger] = cast(
        type[logging.Logger], type("CustomTestLogger", (logging.Logger,), {})
    )
    logging.setLoggerClass(custom_logger_class)
    sample: Sample = github_token()
    named_logger_name: str = f"telescreen.masking.test.{sample.value}"
    named_logger: logging.Logger = logging.getLogger(named_logger_name)
    saved_named_logger_class: type[logging.Logger] = type(named_logger)
    saved_named_handlers: list[logging.Handler] = named_logger.handlers[:]
    saved_named_propagate: bool = named_logger.propagate
    stream: io.StringIO = io.StringIO()
    named_stream: io.StringIO = io.StringIO()
    handler: logging.Handler = logging.StreamHandler(stream)
    named_handler: logging.Handler = logging.StreamHandler(named_stream)
    named_handler.setFormatter(logging.Formatter("%(user_id)s %(name)s %(message)s"))
    root.handlers[:] = [handler, logging.NullHandler()]
    named_logger.handlers[:] = [named_handler]
    named_logger.propagate = False
    try:
        redactor: Redactor = Redactor.default()
        configure_logging("INFO", redactor)
        configure_logging("INFO", redactor)
        assert len(root.handlers) == 1
        assert isinstance(named_logger, custom_logger_class)
        root.info("value=%s", sample.value)
        named_logger.error("named value=%s", sample.value, extra={"user_id": sample.value})
        try:
            raise ValueError(sample.value)
        except ValueError:
            root.exception("caught failure")
        assert_no_leaks(stream.getvalue(), "captured logs")
        assert_no_leaks(named_stream.getvalue(), "named logger logs")
    finally:
        named_logger.handlers[:] = saved_named_handlers
        named_logger.propagate = saved_named_propagate
        named_logger.__class__ = saved_named_logger_class
        logging.setLoggerClass(saved_logger_class)
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        logging.setLogRecordFactory(saved_factory)


def test_uncaught_exception_hook_redacts_traceback() -> None:
    """The installed hook masks exception values before logging them."""
    root: logging.Logger = logging.getLogger()
    saved_handlers: list[logging.Handler] = root.handlers[:]
    saved_level: int = root.level
    saved_factory: RecordFactory = cast(RecordFactory, logging.getLogRecordFactory())
    saved_hook: Callable[[type[BaseException], BaseException, TracebackType | None], None] = (
        sys.excepthook
    )
    stream: io.StringIO = io.StringIO()
    handler: logging.Handler = logging.StreamHandler(stream)
    root.handlers[:] = [handler]
    try:
        redactor: Redactor = Redactor.default()
        configure_logging("ERROR", redactor)
        install_excepthook(redactor)
        sample: Sample = github_token()
        try:
            raise RuntimeError(sample.value)
        except RuntimeError as exception:
            tb: TracebackType | None = exception.__traceback__
            sys.excepthook(type(exception), exception, tb)
        assert_no_leaks(stream.getvalue(), "uncaught traceback")
    finally:
        sys.excepthook = saved_hook
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        logging.setLogRecordFactory(saved_factory)


def test_safe_errors_hide_unknown_exception_messages() -> None:
    """Error summaries expose a stable code and identifier only."""
    sample: Sample = github_token()
    safe: SafeError = safe_error_for(ValueError(sample.value))
    assert safe.code is ErrorCode.INTERNAL_ERROR
    assert safe.error_id
    assert len(new_error_id()) == 36
    assert_no_leaks(repr(safe), "safe error")
    known: SafeError = safe_error_for(TelescreenError(ErrorCode.NOT_FOUND, "static"))
    assert known.code is ErrorCode.NOT_FOUND
