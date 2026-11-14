"""Behavior tests for the shared core contracts."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.config import TelescreenSettings, load_settings
from telescreen.core.durations import parse_duration
from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import (
    JsonKind,
    JsonValue,
    dump_json,
    json_int,
    json_object,
    parse_json,
    parse_yaml,
)
from telescreen.core.secrets import SecretValue, resolve_secret
from telescreen.core.vocabulary import ErrorCode, RuleClass, Severity, severity_for
from tests.support.samples import (
    Sample,
    all_samples,
    assert_no_leaks,
    credit_card,
    find_leaks,
    my_number,
    separator_stripped,
)


@dataclass(frozen=True)
class FakeEnv:
    """Small environment source for settings tests."""

    values: tuple[tuple[str, str], ...] = ()

    def get(self, key: str, /) -> str | None:
        """Return a configured value."""
        return next((value for name, value in self.values if key == name), None)


def test_json_round_trip_and_accessors() -> None:
    """Parse nested values and write canonical JSON."""
    text: str = '{"a":[1,true,null,1.5,"x"]}'
    value: JsonValue = parse_json(text)
    assert dump_json(value) == text
    array: JsonValue | None = value.get("a")
    assert array is not None
    items: tuple[JsonValue, ...] | None = array.as_list()
    assert items is not None
    assert items[0].as_int() == 1
    assert items[1].as_bool() is True
    assert items[1].as_int() is None
    assert items[2].is_null()
    assert items[3].as_float() == 1.5
    assert items[4].as_str() == "x"
    assert parse_json(b"false").kind is JsonKind.BOOL


def test_json_rejects_invalid_input_without_echoing_it() -> None:
    """Invalid JSON yields a safe validation error."""
    with pytest.raises(TelescreenError) as error:
        parse_json("{bad")
    assert error.value.code is ErrorCode.VALIDATION_ERROR
    assert "bad" not in str(error.value)
    with pytest.raises(TelescreenError):
        parse_json("NaN")


def test_yaml_dates_keys_and_canonical_order() -> None:
    """YAML dates are strings while unsupported object keys are rejected."""
    value: JsonValue = parse_yaml("when: 2026-10-08\n")
    date_value: JsonValue | None = value.get("when")
    assert date_value is not None
    assert date_value.as_str() == "2026-10-08"
    with pytest.raises(TelescreenError):
        parse_yaml("1: value\n")
    encoded: str = dump_json(json_object((("z", json_int(1)), ("a", json_int(2)))))
    assert encoded == '{"a":2,"z":1}'
    with pytest.raises(TelescreenError):
        json_object((("same", json_int(1)), ("same", json_int(2))))


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("PT4H", timedelta(hours=4)),
        ("P1D", timedelta(days=1)),
        ("P2W", timedelta(weeks=2)),
        ("PT15M", timedelta(minutes=15)),
    ),
)
def test_parse_supported_durations(text: str, expected: timedelta) -> None:
    """Supported ISO-8601 duration forms parse correctly."""
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ("P1Y", "P1M", "garbage"))
def test_parse_duration_rejects_unsupported_forms(text: str) -> None:
    """Years, calendar months and malformed values are rejected."""
    with pytest.raises(TelescreenError) as error:
        parse_duration(text)
    assert error.value.code is ErrorCode.VALIDATION_ERROR


def test_settings_defaults_and_safe_errors() -> None:
    """Settings defaults and invalid values follow the frozen contract."""
    defaults: TelescreenSettings = load_settings(FakeEnv())
    assert defaults.managed_llm_model == "claude-sonnet-5-5"
    assert defaults.worker_concurrency == 8
    assert defaults.detectors[0].value == "rule"
    with pytest.raises(ConfigError):
        load_settings(FakeEnv((("TELESCREEN_MANAGED_LLM_PROVIDER", "codex"),)))
    with pytest.raises(ConfigError) as error:
        load_settings(FakeEnv((("TELESCREEN_WORKER_CONCURRENCY", "abc"),)))
    assert "abc" not in str(error.value)
    settings: TelescreenSettings = load_settings(
        FakeEnv(
            (("TELESCREEN_DETECTORS", "rule,managed_llm"), ("TELESCREEN_POLICE_ALLOW_HTTP", "TRUE"))
        )
    )
    assert tuple(detector.value for detector in settings.detectors) == ("rule", "managed_llm")
    assert settings.police_allow_http is True


def test_secret_resolution_hides_values(tmp_path: Path) -> None:
    """Environment and file secrets resolve without repr disclosure."""
    value: SecretValue = resolve_secret(
        "env:EXAMPLE_SECRET", FakeEnv((("EXAMPLE_SECRET", "private-value"),))
    )
    assert value.value == "private-value"
    assert "private-value" not in repr(value)
    secret_file: Path = tmp_path / "secret"
    secret_file.write_text("file-secret\n", encoding="utf-8")
    assert resolve_secret(f"file:{secret_file}", FakeEnv()).value == "file-secret"
    with pytest.raises(ConfigError):
        resolve_secret("unknown:value", FakeEnv())


def test_clock_and_identifiers() -> None:
    """Fake time advances and naive datetimes become UTC."""
    clock: FakeClock = FakeClock(datetime(2026, 1, 1))
    clock.advance(timedelta(minutes=2))
    assert clock.now() == datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
    assert ensure_utc(datetime(2026, 1, 1)).tzinfo is UTC
    assert len(new_id()) == 36


def test_severity_covers_all_rule_classes() -> None:
    """Every closed rule class maps to the planned severity."""
    assert {rule: severity_for(rule) for rule in RuleClass} == {
        RuleClass.CREDENTIAL: Severity.HIGH,
        RuleClass.TOKEN: Severity.HIGH,
        RuleClass.PRIVATE_KEY: Severity.HIGH,
        RuleClass.PASSWORD: Severity.HIGH,
        RuleClass.PAYMENT_CARD: Severity.HIGH,
        RuleClass.NATIONAL_ID: Severity.HIGH,
        RuleClass.EMAIL: Severity.MEDIUM,
        RuleClass.PHONE: Severity.MEDIUM,
        RuleClass.ADDRESS: Severity.MEDIUM,
    }


def test_samples_are_valid_and_leak_helpers_report_names_only() -> None:
    """Synthetic samples and leak checks never expose values in repr/errors."""
    card_digits: str = credit_card().value
    card_sum: int = 0
    for index, char in enumerate(reversed(card_digits)):
        digit: int = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        card_sum += digit
    assert card_sum % 10 == 0
    national_id: str = my_number().value
    body: str = national_id[:-1]
    total: int = sum(int(char) * (2 + index % 6) for index, char in enumerate(reversed(body)))
    expected_check: int = (11 - total % 11) % 11
    assert int(national_id[-1]) == (0 if expected_check > 9 else expected_check)
    sample: Sample = all_samples()[2]
    assert sample.value not in repr(sample)
    stripped: str = separator_stripped(sample.value)
    assert sample.name in find_leaks(stripped)
    with pytest.raises(AssertionError) as error:
        assert_no_leaks(sample.value, "test output")
    assert sample.name in str(error.value)
    assert sample.value not in str(error.value)
