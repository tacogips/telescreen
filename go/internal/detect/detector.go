package detect

import (
	"github.com/tacogips/telescreen/go/internal/pytext"
	"sort"
	"strings"
)

type Detector struct {
	tables *pytext.Tables
	rules  []rule
}

func NewDetector(t *pytext.Tables) *Detector { return &Detector{t, newRules(t)} }
func overlap(a, b Span) bool                 { return a.Start < b.End && b.Start < a.End }
func high(c RuleClass) bool {
	switch c {
	case RuleCredential, RuleToken, RulePrivateKey, RulePassword, RulePaymentCard, RuleNationalID:
		return true
	}
	return false
}
func stronger(a, b RawFinding) bool {
	if a.Confidence != b.Confidence {
		return a.Confidence > b.Confidence
	}
	if high(a.RuleClass) != high(b.RuleClass) {
		return high(a.RuleClass)
	}
	return a.Span.End-a.Span.Start > b.Span.End-b.Span.Start
}
func asciiAlnum(r rune) bool {
	return r >= '0' && r <= '9' || r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z'
}
func hexChar(r rune) bool {
	return r >= '0' && r <= '9' || r >= 'a' && r <= 'f' || r >= 'A' && r <= 'F'
}
func uuidSpans(s []rune) []Span {
	out := []Span{}
	for i := 0; i+36 <= len(s); i++ {
		if i > 0 && asciiAlnum(s[i-1]) || i+36 < len(s) && asciiAlnum(s[i+36]) {
			continue
		}
		ok := true
		for j := 0; j < 36; j++ {
			if j == 8 || j == 13 || j == 18 || j == 23 {
				if s[i+j] != '-' {
					ok = false
					break
				}
			} else if !hexChar(s[i+j]) {
				ok = false
				break
			}
		}
		if ok {
			out = append(out, Span{i, i + 36})
			i += 35
		}
	}
	return out
}
func (d *Detector) ScanText(text string) []RawFinding {
	normalized, indices := normalize([]rune(text))
	uuids := uuidSpans(normalized)
	candidates := []RawFinding{}
	for _, r := range d.rules {
		findAll(normalized, d.tables, r.match, func(s, e int) {
			if r.validate != nil && !r.validate(normalized[s:e]) {
				return
			}
			span := Span{s, e}
			for _, u := range uuids {
				if overlap(span, u) && !(s <= u.Start && u.End <= e) {
					return
				}
			}
			if len(r.keywords) > 0 {
				lo := max(0, s-r.keywordWindow)
				hi := min(len(normalized), e+r.keywordWindow)
				window := d.tables.Lower(string(normalized[lo:hi]))
				found := false
				for _, k := range r.keywords {
					if strings.Contains(window, k) {
						found = true
						break
					}
				}
				if !found {
					return
				}
			}
			candidates = append(candidates, RawFinding{r.id, r.class, toOriginal(span, indices), r.confidence})
		})
	}
	sort.SliceStable(candidates, func(i, j int) bool { return stronger(candidates[i], candidates[j]) })
	survivors := []RawFinding{}
	for _, c := range candidates {
		overlaps := []RawFinding{}
		for _, s := range survivors {
			if overlap(s.Span, c.Span) {
				overlaps = append(overlaps, s)
			}
		}
		if len(overlaps) == 0 {
			survivors = append(survivors, c)
			continue
		}
		same := true
		for _, s := range overlaps {
			if s.RuleClass != c.RuleClass {
				same = false
				break
			}
		}
		if !same {
			continue
		}
		kept := []RawFinding{}
		for _, s := range survivors {
			remove := false
			for _, o := range overlaps {
				if s == o {
					remove = true
					break
				}
			}
			if !remove {
				kept = append(kept, s)
			}
		}
		best := c
		span := c.Span
		for _, o := range overlaps {
			span.Start = min(span.Start, o.Span.Start)
			span.End = max(span.End, o.Span.End)
			if stronger(o, best) {
				best = o
			}
		}
		best.Span = span
		survivors = append(kept, best)
	}
	sort.SliceStable(survivors, func(i, j int) bool { return survivors[i].Span.Start < survivors[j].Span.Start })
	return survivors
}
