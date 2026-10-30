package detect

import "github.com/tacogips/telescreen/go/internal/pytext"

// SealEngine converts detector findings into safe output strings.
type SealEngine struct {
	scanner  Scanner
	tables   *pytext.Tables
	key      []byte
	redactor *Redactor
}

var _ Engine = (*SealEngine)(nil)

// NewEngine constructs a sealing engine with a stable fingerprint key.
func NewEngine(s Scanner, t *pytext.Tables, key []byte) *SealEngine {
	return &SealEngine{scanner: s, tables: t, key: append([]byte(nil), key...), redactor: NewRedactor(s, t)}
}

func cut(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		r = r[:n]
	}
	return string(r)
}

func (e *SealEngine) safe(s string) string {
	s = e.redactor.Redact(s)
	if e.redactor.HasHits(s) {
		s = "[REDACTED]"
	}
	return cut(s, 200)
}

func (e *SealEngine) snippet(text []rune, target RawFinding, findings []RawFinding) string {
	lineStart := target.Span.Start
	for lineStart > 0 && text[lineStart-1] != '\n' {
		lineStart--
	}
	lineEnd := target.Span.End
	for lineEnd < len(text) && text[lineEnd] != '\n' {
		lineEnd++
	}
	start := max(lineStart, target.Span.Start-40)
	end := min(lineEnd, target.Span.End+40)
	snippet := append([]rune(nil), text[start:end]...)
	for i := len(findings) - 1; i >= 0; i-- {
		f := findings[i]
		if f.Span.Start >= end || f.Span.End <= start {
			continue
		}
		replacement := []rune(MaskValue(text[f.Span.Start:f.Span.End], f.RuleID, f.RuleClass, e.tables))
		a := max(f.Span.Start, start) - start
		b := min(f.Span.End, end) - start
		next := make([]rune, 0, len(snippet)-(b-a)+len(replacement))
		next = append(next, snippet[:a]...)
		next = append(next, replacement...)
		next = append(next, snippet[b:]...)
		snippet = next
	}
	return cut(string(snippet), 200)
}

// ScanSegment seals all findings, failing closed if no clean fingerprint exists.
func (e *SealEngine) ScanSegment(text, segmentLocator string) ([]Finding, bool) {
	raw := []rune(text)
	findings := e.scanner.ScanText(text)
	result := make([]Finding, 0, len(findings))
	location := e.safe(segmentLocator)
	for _, f := range findings {
		fp, ok := findingFingerprint(string(raw[f.Span.Start:f.Span.End]), f.RuleID, f.RuleClass, e.key, e.redactor, e.tables)
		if !ok {
			return nil, false
		}
		result = append(result, Finding{RuleID: f.RuleID, RuleClass: f.RuleClass, MaskedSnippet: e.safe(e.snippet(raw, f, findings)), Location: location, Confidence: f.Confidence, ExternalFingerprint: fp})
	}
	return result, true
}

// HasHits reports whether the detector finds sensitive content.
func (e *SealEngine) HasHits(s string) bool { return e.redactor.HasHits(s) }
