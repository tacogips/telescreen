package detect

import (
	"fmt"
	"strings"

	"github.com/tacogips/telescreen/go/internal/pytext"
)

// MaskValue renders a detected value without exposing its sensitive portion.
func MaskValue(value []rune, ruleID string, class RuleClass, t *pytext.Tables) string {
	n := len(value)
	switch class {
	case RuleCredential, RuleToken:
		prefixes := []string{"github_pat_", "AKIA", "ASIA", "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "xoxa-", "xoxb-", "xoxp-", "xoxr-", "xoxs-", "AIza"}
		prefix := ""
		for _, p := range prefixes {
			if strings.HasPrefix(string(value), p) && len(p) > len(prefix) {
				prefix = p
			}
		}
		if prefix != "" {
			return fmt.Sprintf("%s%s[%d]", prefix, strings.Repeat("*", min(n-len([]rune(prefix)), 16)), n)
		}
	case RulePrivateKey:
		lines := t.SplitLines(string(value))
		if len(lines) > 0 {
			header := t.Strip(lines[0])
			const begin = "-----BEGIN "
			const end = "-----"
			if strings.HasPrefix(header, begin) && strings.HasSuffix(header, end) {
				middle := strings.TrimSuffix(strings.TrimPrefix(header, begin), end)
				valid := len(middle) > 0
				for _, r := range middle {
					if !(r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == ' ') {
						valid = false
						break
					}
				}
				if valid {
					return fmt.Sprintf("%s [REDACTED %d chars]", header, n)
				}
			}
		}
		return fmt.Sprintf("[REDACTED PRIVATE KEY %d chars]", n)
	case RulePaymentCard:
		digits := []rune{}
		for _, r := range value {
			if _, ok := t.DecimalValue(r); ok {
				digits = append(digits, r)
			}
		}
		return "**** **** **** " + string(digits[max(0, len(digits)-4):])
	case RuleNationalID:
		return "************"
	case RuleEmail:
		pos := -1
		for i, r := range value {
			if r == '@' {
				pos = i
			}
		}
		if pos >= 0 {
			local := ""
			if pos > 0 {
				local = string(value[:1])
			}
			return local + "**\u2060*@" + string(value[pos+1:])
		}
	case RulePhone:
		total := 0
		for _, r := range value {
			if t.IsDigit(r) {
				total++
			}
		}
		out := append([]rune(nil), value...)
		seen := 0
		for i, r := range value {
			if t.IsDigit(r) {
				seen++
				if seen <= total-2 {
					out[i] = '*'
				}
			}
		}
		return string(out)
	case RuleAddress:
		return "[ADDRESS REDACTED]"
	}
	return fmt.Sprintf("********[%d]", n)
}
