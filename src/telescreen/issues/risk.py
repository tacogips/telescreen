"""Default ordinal risk scoring for sensitive-data findings."""

from dataclasses import dataclass
from datetime import timedelta

from telescreen.core.risk_weights import RiskWeights
from telescreen.core.vocabulary import AssetTier, RuleClass, Severity, Visibility
from telescreen.ports.policy import RiskInputs


@dataclass(frozen=True)
class DefaultRiskScorer:
    """Score severity, exposure, asset tier and issue age with configured weights."""

    weights: RiskWeights

    def score(self, inputs: RiskInputs) -> int:
        """Return a clamped 0-100 ordinal score."""
        age: timedelta = max(timedelta(0), inputs.now - inputs.first_seen)
        age_weight: float = (
            self.weights.age_under_1d
            if age < timedelta(days=1)
            else self.weights.age_under_7d
            if age < timedelta(days=7)
            else self.weights.age_under_30d
            if age < timedelta(days=30)
            else self.weights.age_over_30d
        )
        raw_score: float = (
            100.0
            * self._severity_weight(inputs.severity)
            * self._exposure_weight(inputs.visibility)
            * self._asset_weight(inputs.asset_tier)
            * age_weight
        )
        return min(100, max(0, round(raw_score)))

    def asset_tier(self, rule_id: str, rule_class: RuleClass) -> AssetTier:
        """Map a detector rule id to its specific tier or its class fallback."""
        if rule_id in ("aws_access_key_id", "aws_secret_access_key", "private_key"):
            return AssetTier.CLOUD_ROOT
        if rule_id in ("slack_token", "google_api_key"):
            return AssetTier.DEPLOY_TOKEN
        if rule_id in ("github_token", "jwt"):
            return AssetTier.PERSONAL_TOKEN
        if rule_id == "password_assignment":
            return AssetTier.SECRET
        if rule_id in ("credit_card", "my_number"):
            return AssetTier.PII_HIGH
        if rule_class in (RuleClass.CREDENTIAL, RuleClass.TOKEN):
            return AssetTier.PERSONAL_TOKEN
        if rule_class is RuleClass.PRIVATE_KEY:
            return AssetTier.CLOUD_ROOT
        if rule_class is RuleClass.PASSWORD:
            return AssetTier.SECRET
        if rule_class in (RuleClass.PAYMENT_CARD, RuleClass.NATIONAL_ID):
            return AssetTier.PII_HIGH
        return AssetTier.PII

    def _severity_weight(self, severity: Severity) -> float:
        if severity is Severity.CRITICAL:
            return self.weights.severity_critical
        if severity is Severity.HIGH:
            return self.weights.severity_high
        return self.weights.severity_medium

    def _exposure_weight(self, visibility: Visibility) -> float:
        if visibility is Visibility.PUBLIC:
            return self.weights.exposure_public
        if visibility is Visibility.ANYONE_WITH_LINK:
            return self.weights.exposure_anyone_with_link
        if visibility is Visibility.EXTERNAL_SHARED:
            return self.weights.exposure_external_shared
        if visibility is Visibility.DOMAIN:
            return self.weights.exposure_domain
        if visibility is Visibility.INTERNAL:
            return self.weights.exposure_internal
        if visibility is Visibility.PRIVATE:
            return self.weights.exposure_private
        return self.weights.exposure_unknown

    def _asset_weight(self, tier: AssetTier | None) -> float:
        if tier is AssetTier.CLOUD_ROOT:
            return self.weights.asset_cloud_root
        if tier is AssetTier.DEPLOY_TOKEN:
            return self.weights.asset_deploy_token
        if tier is AssetTier.PERSONAL_TOKEN:
            return self.weights.asset_personal_token
        if tier is AssetTier.SECRET:
            return self.weights.asset_secret
        if tier is AssetTier.PII_HIGH:
            return self.weights.asset_pii_high
        return self.weights.asset_pii
