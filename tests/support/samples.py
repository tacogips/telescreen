"""Synthetic sensitive data samples assembled at runtime."""

from dataclasses import dataclass, field

from telescreen.core.vocabulary import RuleClass


@dataclass(frozen=True)
class Sample:
    """A named test sample whose raw value is hidden from repr."""

    name: str
    rule_class: RuleClass
    value: str = field(repr=False)


def aws_access_key_id() -> Sample:
    """Return a synthetic AWS access key identifier."""
    return Sample("aws_access_key_id", RuleClass.CREDENTIAL, "AKIA" + "IOSFODNN7EXAMPLE")


def aws_secret_access_key() -> Sample:
    """Return a synthetic AWS secret access key."""
    return Sample(
        "aws_secret_access_key",
        RuleClass.CREDENTIAL,
        "wJalrXUtnFEMI/" + "K7MDENG/" + "bPxRfiCYEXAMPLEKEY",
    )


def github_token() -> Sample:
    """Return a synthetic GitHub token-shaped sample."""
    return Sample(
        "github_token", RuleClass.TOKEN, "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + "abcdefghijkl"
    )


def slack_token() -> Sample:
    """Return a synthetic Slack token-shaped sample."""
    return Sample(
        "slack_token",
        RuleClass.TOKEN,
        "xoxb-" + "123456789012-" + "123456789012-" + "abcdefghijklmnopqrstuvwx",
    )


def google_api_key() -> Sample:
    """Return a synthetic Google API key-shaped sample."""
    return Sample(
        "google_api_key", RuleClass.CREDENTIAL, "AIza" + "SyA" + "1234567890" + "abcdefghijklmno"
    )


def pem_private_key() -> Sample:
    """Return a synthetic PEM private key marker sample."""
    value: str = (
        "-----BEGIN " + "RSA PRIVATE KEY-----\n" + "sample\n" + "-----END RSA PRIVATE KEY-----"
    )
    return Sample("pem_private_key", RuleClass.PRIVATE_KEY, value)


def jwt_token() -> Sample:
    """Return a synthetic JWT-shaped sample."""
    return Sample(
        "jwt_token",
        RuleClass.TOKEN,
        "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjMifQ" + "." + "signature",
    )


def password_assignment() -> Sample:
    """Return a synthetic password assignment."""
    return Sample(
        "password_assignment",
        RuleClass.PASSWORD,
        "password=" + "correct" + "-" + "horse" + "-" + "battery",
    )


def credit_card() -> Sample:
    """Return a synthetic Luhn-valid Visa-shaped card number."""
    return Sample("credit_card", RuleClass.PAYMENT_CARD, "4" + "1" * 15)


def my_number() -> Sample:
    """Return a synthetic Japanese My Number with a valid check digit."""
    body: str = "12345678901"
    total: int = sum(int(digit) * (2 + index % 6) for index, digit in enumerate(reversed(body)))
    check: int = (11 - total % 11) % 11
    digit: int = 0 if check > 9 else check
    return Sample("my_number", RuleClass.NATIONAL_ID, body + str(digit))


def email_address() -> Sample:
    """Return a synthetic email address."""
    return Sample("email_address", RuleClass.EMAIL, "sample" + "@example.invalid")


def jp_mobile() -> Sample:
    """Return a synthetic Japanese mobile number."""
    return Sample("jp_mobile", RuleClass.PHONE, "090" + "1234" + "5678")


def jp_landline() -> Sample:
    """Return a synthetic Japanese landline number."""
    return Sample("jp_landline", RuleClass.PHONE, "03" + "1234" + "5678")


def jp_address() -> Sample:
    """Return a synthetic Japanese address."""
    return Sample("jp_address", RuleClass.ADDRESS, "東京都" + "千代田区" + "千代田" + "1-1")


def all_samples() -> tuple[Sample, ...]:
    """Return the complete synthetic sample set."""
    return (
        aws_access_key_id(),
        aws_secret_access_key(),
        github_token(),
        slack_token(),
        google_api_key(),
        pem_private_key(),
        jwt_token(),
        password_assignment(),
        credit_card(),
        my_number(),
        email_address(),
        jp_mobile(),
        jp_landline(),
        jp_address(),
    )


def separator_stripped(value: str) -> str:
    """Remove spaces and hyphens from a sample."""
    return value.replace(" ", "").replace("-", "")


def find_leaks(text: str) -> tuple[str, ...]:
    """Return sample names found literally or without common separators."""
    found: list[str] = []
    for sample in all_samples():
        if sample.value and (sample.value in text or separator_stripped(sample.value) in text):
            found.append(sample.name)
    return tuple(found)


def assert_no_leaks(text: str, where: str) -> None:
    """Assert no known sample values occur, naming samples but never values."""
    leaks: tuple[str, ...] = find_leaks(text)
    if leaks:
        raise AssertionError(f"sample leaks in {where}: {', '.join(leaks)}")
