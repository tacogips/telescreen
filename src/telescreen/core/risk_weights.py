"""Validated ordinal risk weighting settings."""

from dataclasses import dataclass, replace

from telescreen.core.errors import ConfigError


@dataclass(frozen=True)
class RiskWeights:
    """Weights for severity, exposure, asset tier, and age bands."""

    severity_critical: float = 1.0
    severity_high: float = 0.75
    severity_medium: float = 0.45
    exposure_public: float = 1.0
    exposure_anyone_with_link: float = 0.9
    exposure_external_shared: float = 0.8
    exposure_domain: float = 0.6
    exposure_unknown: float = 0.6
    exposure_internal: float = 0.45
    exposure_private: float = 0.3
    asset_cloud_root: float = 1.0
    asset_deploy_token: float = 0.85
    asset_personal_token: float = 0.75
    asset_secret: float = 0.65
    asset_pii_high: float = 0.6
    asset_pii: float = 0.4
    age_under_1d: float = 0.7
    age_under_7d: float = 0.8
    age_under_30d: float = 0.9
    age_over_30d: float = 1.0


def parse_risk_weights(raw: str | None) -> RiskWeights:
    """Parse explicit comma-separated weight overrides."""
    weights: RiskWeights = RiskWeights()
    if raw is None or not raw.strip():
        return weights
    seen: set[str] = set()
    for entry in raw.split(","):
        key_value: tuple[str, ...] = tuple(part.strip() for part in entry.split("=", 1))
        if len(key_value) != 2 or not key_value[0] or not key_value[1]:
            raise ConfigError("TELESCREEN_RISK_WEIGHTS is invalid")
        key: str = key_value[0]
        if key in seen:
            raise ConfigError("TELESCREEN_RISK_WEIGHTS is invalid")
        seen.add(key)
        try:
            value: float = float(key_value[1])
        except ValueError:
            raise ConfigError("TELESCREEN_RISK_WEIGHTS is invalid") from None
        if not 0.0 < value <= 1.0:
            raise ConfigError("TELESCREEN_RISK_WEIGHTS is invalid")
        weights = _replace_weight(weights, key, value)
    return weights


def _replace_weight(weights: RiskWeights, key: str, value: float) -> RiskWeights:
    """Replace one recognized risk weight without dynamic keyword unpacking."""
    if key == "severity.critical":
        return replace(weights, severity_critical=value)
    if key == "severity.high":
        return replace(weights, severity_high=value)
    if key == "severity.medium":
        return replace(weights, severity_medium=value)
    if key == "exposure.public":
        return replace(weights, exposure_public=value)
    if key == "exposure.anyone_with_link":
        return replace(weights, exposure_anyone_with_link=value)
    if key == "exposure.external_shared":
        return replace(weights, exposure_external_shared=value)
    if key == "exposure.domain":
        return replace(weights, exposure_domain=value)
    if key == "exposure.unknown":
        return replace(weights, exposure_unknown=value)
    if key == "exposure.internal":
        return replace(weights, exposure_internal=value)
    if key == "exposure.private":
        return replace(weights, exposure_private=value)
    if key == "asset.cloud_root":
        return replace(weights, asset_cloud_root=value)
    if key == "asset.deploy_token":
        return replace(weights, asset_deploy_token=value)
    if key == "asset.personal_token":
        return replace(weights, asset_personal_token=value)
    if key == "asset.secret":
        return replace(weights, asset_secret=value)
    if key == "asset.pii_high":
        return replace(weights, asset_pii_high=value)
    if key == "asset.pii":
        return replace(weights, asset_pii=value)
    if key == "age.under_1d":
        return replace(weights, age_under_1d=value)
    if key == "age.under_7d":
        return replace(weights, age_under_7d=value)
    if key == "age.under_30d":
        return replace(weights, age_under_30d=value)
    if key == "age.over_30d":
        return replace(weights, age_over_30d=value)
    raise ConfigError("TELESCREEN_RISK_WEIGHTS is invalid")
