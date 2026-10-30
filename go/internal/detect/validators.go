package detect

import (
	"encoding/base64"
	"encoding/json"
	"math"
	"strings"
	"unicode/utf8"

	"github.com/tacogips/telescreen/go/internal/pytext"
)

func luhnValid(digits []rune, t *pytext.Tables) bool {
	total := 0
	for i := len(digits) - 1; i >= 0; i-- {
		d, ok := t.DecimalValue(digits[i])
		if !ok {
			return false
		}
		if (len(digits)-1-i)%2 == 1 {
			d *= 2
			if d > 9 {
				d -= 9
			}
		}
		total += d
	}
	return len(digits) > 0 && total%10 == 0
}

func knownCardIIN(digits []rune, t *pytext.Tables) bool {
	prefix := func(s string) bool { return strings.HasPrefix(string(digits), s) }
	number := func(n int) int {
		if len(digits) < n {
			return -1
		}
		v := 0
		for _, r := range digits[:n] {
			d, ok := t.DecimalValue(r)
			if !ok {
				return -1
			}
			v = v*10 + d
		}
		return v
	}
	if prefix("4") || prefix("34") || prefix("37") || prefix("6011") || prefix("65") || prefix("36") || prefix("38") {
		return true
	}
	a, b, c := number(2), number(3), number(4)
	return a >= 51 && a <= 55 || b >= 300 && b <= 305 || c >= 2221 && c <= 2720 || c >= 3528 && c <= 3589
}

func myNumberValid(digits []rune, t *pytext.Tables) bool {
	values := make([]int, 0, 12)
	for _, r := range digits {
		if d, ok := t.DecimalValue(r); ok {
			values = append(values, d)
		}
	}
	if len(values) != 12 {
		return false
	}
	total := 0
	for i := 0; i < 11; i++ {
		weight := i + 2
		if i >= 6 {
			weight = i - 4
		}
		total += values[10-i] * weight
	}
	rem := total % 11
	expected := 0
	if rem > 1 {
		expected = 11 - rem
	}
	return values[11] == expected
}

func shannonEntropy(text []rune) float64 {
	if len(text) == 0 {
		return 0
	}
	counts := make(map[rune]int)
	order := make([]rune, 0)
	for _, r := range text {
		if counts[r] == 0 {
			order = append(order, r)
		}
		counts[r]++
	}
	result := 0.0
	for _, r := range order {
		p := float64(counts[r]) / float64(len(text))
		result -= p * math.Log2(p)
	}
	return result
}

// pythonJSONConstants replaces Python's non-standard numeric constants outside strings.
func pythonJSONConstants(s string) string {
	var out strings.Builder
	inString, escaped := false, false
	for i := 0; i < len(s); {
		c := s[i]
		if inString {
			out.WriteByte(c)
			i++
			if escaped {
				escaped = false
			} else if c == '\\' {
				escaped = true
			} else if c == '"' {
				inString = false
			}
			continue
		}
		if c == '"' {
			inString = true
			out.WriteByte(c)
			i++
			continue
		}
		replaced := false
		for _, constant := range []string{"-Infinity", "Infinity", "NaN"} {
			if strings.HasPrefix(s[i:], constant) {
				out.WriteByte('0')
				i += len(constant)
				replaced = true
				break
			}
		}
		if !replaced {
			out.WriteByte(c)
			i++
		}
	}
	return out.String()
}

func jwtHeaderValid(token []rune) bool {
	segments := strings.Split(string(token), ".")
	if len(segments) != 3 {
		return false
	}
	var filtered strings.Builder
	for _, r := range segments[0] {
		if r > 127 {
			return false
		}
		if r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '-' || r == '_' || r == '=' {
			filtered.WriteRune(r)
		}
	}
	encoded := filtered.String()
	encoded += strings.Repeat("=", (4-len(segments[0])%4)%4)
	data, err := base64.URLEncoding.DecodeString(encoded)
	if err != nil || !utf8.Valid(data) {
		return false
	}
	var value map[string]json.RawMessage
	if json.Unmarshal([]byte(pythonJSONConstants(string(data))), &value) != nil || value == nil {
		return false
	}
	_, ok := value["alg"]
	return ok
}

func isPlaceholder(value []rune, t *pytext.Tables) bool {
	lower := t.Lower(t.Strip(string(value)))
	return lower == "***" || lower == "changeme" || lower == "example" || strings.HasPrefix(lower, "${") && strings.HasSuffix(lower, "}") || strings.HasPrefix(lower, "<") && strings.HasSuffix(lower, ">")
}
