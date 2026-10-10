"""Placement policy document parsing and validation tests."""

import pytest

from telescreen.core.json import dump_json
from telescreen.core.vocabulary import SourceFormat, Visibility
from telescreen.issues.policy.loader import (
    PolicyValidationError,
    load_policy,
    policy_from_json,
    policy_to_json,
)
from telescreen.issues.policy.model import PolicyDefinition

EXAMPLE: str = """name: hr-records
effect: allow
match:
  rule_classes: [national_id, address, phone, email]
  sources: [gdrive, gdocs, gsheets]
  locations:
    gdrive_drives: [shared-drive]
    gdrive_folders: [parent-folder]
    slack_channels: [channel-1]
    github_repos: [Example/Records, Example/*]
condition:
  max_exposure: internal
  max_age_days: 365
owner_team: hr
rationale: HR keeps employee records here
"""


def test_load_yaml_and_round_trip_compiled_json() -> None:
    """Parse the documented policy shape and preserve normalized selectors."""
    policy: PolicyDefinition = load_policy(EXAMPLE, SourceFormat.YAML)
    assert policy.name == "hr-records"
    assert policy.locations.github_repos == ("example/records", "example/*")
    assert policy.max_exposure is Visibility.INTERNAL
    assert policy.max_age_days == 365
    assert policy.route_to_owner is True
    assert policy_from_json(dump_json(policy_to_json(policy))) == policy


@pytest.mark.parametrize(
    ("source", "path", "code"),
    (
        ('{"name":"policy","effect":"allow","match":{"foo":true}}', "/match/foo", "unknown_key"),
        ('{"name":"policy","effect":"violate","condition":{}}', "/condition", "allow_only"),
        (
            '{"name":"policy","effect":"allow","condition":{"max_exposure":"unknown"}}',
            "/condition/max_exposure",
            "invalid_value",
        ),
        (
            '{"name":"policy","effect":"allow","match":{"locations":{"github_repos":["org/a*"]}}}',
            "/match/locations/github_repos/0",
            "invalid_value",
        ),
        (
            '{"name":"policy","effect":"allow","condition":{"max_age_days":36501}}',
            "/condition/max_age_days",
            "invalid_value",
        ),
    ),
)
def test_invalid_documents_report_only_paths_and_codes(source: str, path: str, code: str) -> None:
    """Reject invalid values without putting source values into diagnostics."""
    with pytest.raises(PolicyValidationError) as caught:
        load_policy(source, SourceFormat.JSON)
    assert (path, code) in tuple((issue.path, issue.code) for issue in caught.value.issues)
    assert source not in str(caught.value)


def test_json_policy_is_equivalent_to_yaml() -> None:
    """Load the same policy fields from JSON source format."""
    policy: PolicyDefinition = load_policy(
        '{"name":"safe","effect":"allow","match":{"sources":["gdrive"]},'
        '"condition":{"max_exposure":"private"}}',
        SourceFormat.JSON,
    )
    assert policy.name == "safe"
    assert policy.max_exposure is Visibility.PRIVATE


def test_violate_compiled_policy_omits_empty_condition() -> None:
    """Round-trip an unconditional violation without manufacturing a condition."""
    policy: PolicyDefinition = load_policy(
        '{"name":"blocked","effect":"violate"}', SourceFormat.JSON
    )
    assert policy_from_json(dump_json(policy_to_json(policy))) == policy
