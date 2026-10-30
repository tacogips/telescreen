package detect

import (
	"strings"
	"testing"
)

func TestEngineMasksSegmentAndLocator(t *testing.T) {
	var data struct {
		Cases []struct {
			Text string `json:"text_b64"`
		} `json:"cases"`
	}
	parityRead(t, "sealing", &data)
	if len(data.Cases) == 0 {
		t.Fatal("empty sealing fixture")
	}
	secret := parityDecode(t, data.Cases[0].Text)
	email := "alice" + "@" + "example.test"
	tables := parityTables(t)
	scanner := NewDetector(tables)
	engine := NewEngine(scanner, tables, []byte("test-key-for-engine"))
	findings, ok := engine.ScanSegment(secret+" "+email, "locator "+secret)
	if !ok || len(findings) != 2 {
		t.Fatalf("expected two findings, got %d (ok=%v)", len(findings), ok)
	}
	redactor := NewRedactor(scanner, tables)
	for _, f := range findings {
		if strings.Contains(f.MaskedSnippet, secret) || strings.Contains(f.MaskedSnippet, email) || engine.HasHits(f.MaskedSnippet) {
			t.Error("unsafe snippet")
		}
		if f.Location != redactor.Redact("locator "+secret) || engine.HasHits(f.Location) {
			t.Error("unsafe location")
		}
	}
}

type allHexScanner struct{}

func (allHexScanner) ScanText(text string) []RawFinding {
	if text == "x" || len(text) == 64 {
		return []RawFinding{{RuleID: "stub", RuleClass: RuleCredential, Span: Span{Start: 0, End: 1}, Confidence: 1}}
	}
	return nil
}
func TestEngineRejectsAllFlaggedFingerprints(t *testing.T) {
	tables := parityTables(t)
	engine := NewEngine(allHexScanner{}, tables, []byte("key"))
	findings, ok := engine.ScanSegment("x", "locator")
	if ok || findings != nil {
		t.Fatal("expected fingerprint derivation failure")
	}
}
