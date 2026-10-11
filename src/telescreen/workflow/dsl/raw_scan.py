"""Raw-value scanning rules for workflow source documents."""

from telescreen.core.vocabulary import RuleClass
from telescreen.masking.redact import Redactor

_EMAIL_RECIPIENT_TERMINATORS: frozenset[str] = frozenset(
    (" ", "\t", "\n", "\r", "\f", "\v", '"', "'", ",", "]", "}")
)


def source_has_raw_values(redactor: Redactor, text: str) -> bool:
    """Return whether source has sensitive findings beyond explicit email targets."""
    for finding in redactor.detector.scan_text(text):
        if finding.rule_class is not RuleClass.EMAIL:
            return True
        if finding.span.start < 6 or text[finding.span.start - 6 : finding.span.start] != "email:":
            return True
        if (
            finding.span.end < len(text)
            and text[finding.span.end] not in _EMAIL_RECIPIENT_TERMINATORS
        ):
            return True
    return False
