package detect

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"testing"

	"github.com/tacogips/telescreen/go/internal/pytext"
)

func parityDecode(t *testing.T, s string) string {
	t.Helper()
	b, err := base64.StdEncoding.DecodeString(s)
	if err != nil {
		t.Fatal("invalid fixture encoding")
	}
	key := []byte("telescreen-go-parity-fixture")
	for i := range b {
		b[i] ^= key[i%len(key)]
	}
	return string(b)
}
func parityRead(t *testing.T, name string, target interface{}) {
	t.Helper()
	b, err := os.ReadFile("../../testdata/parity/" + name + ".json")
	if err != nil {
		t.Fatal(err)
	}
	var header struct {
		Obfuscation string `json:"obfuscation"`
	}
	if json.Unmarshal(b, &header) != nil || header.Obfuscation != "xor-v1:telescreen-go-parity-fixture" {
		t.Fatal("invalid fixture metadata")
	}
	if err := json.Unmarshal(b, target); err != nil {
		t.Fatal(err)
	}
}
func parityTables(t *testing.T) *pytext.Tables {
	t.Helper()
	tables, err := pytext.Default()
	if err != nil {
		t.Fatal(err)
	}
	return tables
}
func TestSealingParity(t *testing.T) {
	var keys struct {
		Key string `json:"key_b64"`
	}
	parityRead(t, "fingerprints", &keys)
	var data struct {
		Cases []struct {
			Text     string `json:"text_b64"`
			Locator  string `json:"locator"`
			Findings []struct {
				ID          string    `json:"rule_id"`
				Class       RuleClass `json:"rule_class"`
				Confidence  float64   `json:"confidence"`
				Snippet     string    `json:"masked_snippet_b64"`
				Location    string    `json:"location"`
				Fingerprint string    `json:"external_fingerprint"`
			} `json:"findings"`
		} `json:"cases"`
	}
	parityRead(t, "sealing", &data)
	if len(data.Cases) == 0 {
		t.Fatal("empty sealing fixture")
	}
	tables := parityTables(t)
	engine := NewEngine(NewDetector(tables), tables, []byte(parityDecode(t, keys.Key)))
	for i, c := range data.Cases {
		got, ok := engine.ScanSegment(parityDecode(t, c.Text), c.Locator)
		if !ok || len(got) != len(c.Findings) {
			t.Errorf("case %d count or status mismatch", i)
			continue
		}
		for j, w := range c.Findings {
			f := got[j]
			if f.RuleID != w.ID || f.RuleClass != w.Class || f.Confidence != w.Confidence || f.MaskedSnippet != parityDecode(t, w.Snippet) || f.Location != w.Location || f.ExternalFingerprint != w.Fingerprint {
				t.Errorf("case %d finding %d mismatch", i, j)
			}
		}
	}
	t.Logf("checked %d sealing cases", len(data.Cases))
}
func TestRedactorParity(t *testing.T) {
	var data struct {
		Cases []struct {
			Text     string `json:"text_b64"`
			Redacted string `json:"redacted_b64"`
			Hits     bool   `json:"has_hits"`
		} `json:"cases"`
	}
	parityRead(t, "redactor", &data)
	if len(data.Cases) == 0 {
		t.Fatal("empty redactor fixture")
	}
	tables := parityTables(t)
	redactor := NewRedactor(NewDetector(tables), tables)
	for i, c := range data.Cases {
		text := parityDecode(t, c.Text)
		if redactor.HasHits(text) != c.Hits || redactor.Redact(text) != parityDecode(t, c.Redacted) {
			t.Errorf("case %d mismatch", i)
		}
	}
	t.Logf("checked %d redactor cases", len(data.Cases))
}
func TestFingerprintParity(t *testing.T) {
	var data struct {
		Key      string `json:"key_b64"`
		External []struct {
			Value string `json:"value_b64"`
			FP    string `json:"fp"`
		} `json:"external"`
		Item []struct {
			Value string `json:"item_key_b64"`
			FP    string `json:"fp"`
		} `json:"item"`
		Finding []struct {
			Raw        string    `json:"raw_b64"`
			Normalized string    `json:"normalized_b64"`
			ID         string    `json:"rule_id"`
			Class      RuleClass `json:"rule_class"`
			FP         string    `json:"fp"`
			Derived    int       `json:"derived_n"`
		} `json:"finding"`
	}
	parityRead(t, "fingerprints", &data)
	if len(data.External) == 0 || len(data.Item) == 0 || len(data.Finding) == 0 {
		t.Fatal("empty fingerprint fixture")
	}
	key := []byte(parityDecode(t, data.Key))
	tables := parityTables(t)
	redactor := NewRedactor(NewDetector(tables), tables)
	for i, c := range data.External {
		if ExternalFingerprint(parityDecode(t, c.Value), key) != c.FP {
			t.Errorf("external case %d mismatch", i)
		}
	}
	for i, c := range data.Item {
		if ItemFingerprint(parityDecode(t, c.Value), key) != c.FP {
			t.Errorf("item case %d mismatch", i)
		}
	}
	derived := 0
	for i, c := range data.Finding {
		raw := parityDecode(t, c.Raw)
		got, ok := findingFingerprint(raw, c.ID, c.Class, key, redactor, tables)
		if c.Derived > 0 {
			derived++
		}
		if !ok || got != c.FP || normalizeForFingerprint(raw, c.Class, tables) != parityDecode(t, c.Normalized) {
			t.Errorf("finding case %d mismatch", i)
		}
	}
	if derived < 2 {
		t.Fatalf("only %d derived cases", derived)
	}
	t.Logf("checked %d external, %d item, %d finding cases (%d derived)", len(data.External), len(data.Item), len(data.Finding), derived)
}
