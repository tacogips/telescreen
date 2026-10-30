package detect

import "github.com/tacogips/telescreen/go/internal/pytext"

// Redactor masks rule findings in output strings.
type Redactor struct {
	scanner Scanner
	tables  *pytext.Tables
}

func NewRedactor(s Scanner, t *pytext.Tables) *Redactor { return &Redactor{scanner: s, tables: t} }

func (r *Redactor) Redact(text string) string {
	findings := r.scanner.ScanText(text)
	original := []rune(text)
	masked := append([]rune(nil), original...)
	for i := len(findings) - 1; i >= 0; i-- {
		f := findings[i]
		replacement := []rune(MaskValue(original[f.Span.Start:f.Span.End], f.RuleID, f.RuleClass, r.tables))
		next := make([]rune, 0, f.Span.Start+len(replacement)+len(masked)-f.Span.End)
		next = append(next, masked[:f.Span.Start]...)
		next = append(next, replacement...)
		next = append(next, masked[f.Span.End:]...)
		masked = next
	}
	return string(masked)
}

func (r *Redactor) HasHits(text string) bool { return len(r.scanner.ScanText(text)) > 0 }
