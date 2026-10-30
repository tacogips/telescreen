package detect

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"strconv"
	"strings"

	"github.com/tacogips/telescreen/go/internal/pytext"
	"golang.org/x/text/unicode/norm"
)

// ExternalFingerprint returns the keyed fingerprint of an external value.
func ExternalFingerprint(value string, key []byte) string {
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(value))
	return hex.EncodeToString(mac.Sum(nil))
}

// ItemFingerprint returns the keyed fingerprint of an item key.
func ItemFingerprint(itemKey string, key []byte) string {
	return ExternalFingerprint(itemKey, key)
}

func normalizeForFingerprint(raw string, class RuleClass, t *pytext.Tables) string {
	normalized := norm.NFKC.String(raw)
	switch class {
	case RulePaymentCard, RuleNationalID, RulePhone:
		normalized = strings.ReplaceAll(normalized, " ", "")
		return strings.ReplaceAll(normalized, "-", "")
	case RuleEmail:
		return t.Lower(normalized)
	default:
		return normalized
	}
}

func findingFingerprint(raw, ruleID string, class RuleClass, key []byte, redactor *Redactor, t *pytext.Tables) (string, bool) {
	message := "v1\x00" + ruleID + "\x00" + normalizeForFingerprint(raw, class, t)
	for n := 0; n <= 16; n++ {
		candidate := message
		if n > 0 {
			candidate += "\x00" + strconv.Itoa(n)
		}
		fp := ExternalFingerprint(candidate, key)
		if !redactor.HasHits(fp) {
			return fp, true
		}
	}
	return "", false
}
