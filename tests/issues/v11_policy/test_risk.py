"""Ordinal risk score and asset tier tests."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from telescreen.core.risk_weights import RiskWeights
from telescreen.core.vocabulary import AssetTier, RuleClass, Severity, Visibility
from telescreen.issues.risk import DefaultRiskScorer
from telescreen.ports.policy import RiskInputs

NOW: datetime = datetime(2026, 10, 10, tzinfo=UTC)


def test_public_aws_key_ten_days_old_scores_ninety() -> None:
    """Use the documented public AWS key default score."""
    scorer: DefaultRiskScorer = DefaultRiskScorer(RiskWeights())
    inputs: RiskInputs = RiskInputs(
        Severity.CRITICAL,
        Visibility.PUBLIC,
        AssetTier.CLOUD_ROOT,
        NOW - timedelta(days=10),
        NOW,
    )
    assert scorer.score(inputs) == 90


def test_private_pii_under_one_day_scores_four() -> None:
    """Use medium severity, private visibility and PII fallback factors."""
    scorer: DefaultRiskScorer = DefaultRiskScorer(RiskWeights())
    inputs: RiskInputs = RiskInputs(
        Severity.MEDIUM,
        Visibility.PRIVATE,
        AssetTier.PII,
        NOW - timedelta(hours=1),
        NOW,
    )
    assert scorer.score(inputs) == 4


def test_weight_override_and_score_clamping_apply() -> None:
    """Use configured factor values and clamp rounded output to the ordinal range."""
    weights: RiskWeights = replace(RiskWeights(), severity_critical=0.5, exposure_public=0.5)
    scorer: DefaultRiskScorer = DefaultRiskScorer(weights)
    inputs: RiskInputs = RiskInputs(
        Severity.CRITICAL, Visibility.PUBLIC, AssetTier.CLOUD_ROOT, NOW, NOW
    )
    assert scorer.score(inputs) == 18
    assert scorer.score(replace(inputs, asset_tier=None)) == 7


def test_asset_rule_ids_and_class_fallbacks() -> None:
    """Resolve every named asset tier and the documented class fallbacks."""
    scorer: DefaultRiskScorer = DefaultRiskScorer(RiskWeights())
    assert scorer.asset_tier("aws_access_key_id", RuleClass.CREDENTIAL) is AssetTier.CLOUD_ROOT
    assert scorer.asset_tier("aws_secret_access_key", RuleClass.CREDENTIAL) is AssetTier.CLOUD_ROOT
    assert scorer.asset_tier("private_key", RuleClass.PRIVATE_KEY) is AssetTier.CLOUD_ROOT
    assert scorer.asset_tier("slack_token", RuleClass.TOKEN) is AssetTier.DEPLOY_TOKEN
    assert scorer.asset_tier("google_api_key", RuleClass.CREDENTIAL) is AssetTier.DEPLOY_TOKEN
    assert scorer.asset_tier("github_token", RuleClass.TOKEN) is AssetTier.PERSONAL_TOKEN
    assert scorer.asset_tier("jwt", RuleClass.TOKEN) is AssetTier.PERSONAL_TOKEN
    assert scorer.asset_tier("password_assignment", RuleClass.PASSWORD) is AssetTier.SECRET
    assert scorer.asset_tier("credit_card", RuleClass.PAYMENT_CARD) is AssetTier.PII_HIGH
    assert scorer.asset_tier("my_number", RuleClass.NATIONAL_ID) is AssetTier.PII_HIGH
    assert scorer.asset_tier("unknown", RuleClass.PRIVATE_KEY) is AssetTier.CLOUD_ROOT
    assert scorer.asset_tier("unknown", RuleClass.CREDENTIAL) is AssetTier.PERSONAL_TOKEN
    assert scorer.asset_tier("unknown", RuleClass.PASSWORD) is AssetTier.SECRET
    assert scorer.asset_tier("unknown", RuleClass.NATIONAL_ID) is AssetTier.PII_HIGH
    assert scorer.asset_tier("unknown", RuleClass.EMAIL) is AssetTier.PII
