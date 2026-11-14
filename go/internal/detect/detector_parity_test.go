package detect

import (
	"encoding/base64"
	"encoding/json"
	"github.com/tacogips/telescreen/go/internal/pytext"
	"os"
	"testing"
)

func TestDetectorParity(t *testing.T) {
	var fixture struct {
		Meta struct {
			Generated int `json:"generated"`
		} `json:"meta"`
		Obfuscation string `json:"obfuscation"`
		Cases       []struct {
			Text     string `json:"text_b64"`
			Findings []struct {
				ID         string    `json:"rule_id"`
				Class      RuleClass `json:"rule_class"`
				Start      int       `json:"start"`
				End        int       `json:"end"`
				Confidence float64   `json:"confidence"`
			} `json:"findings"`
		} `json:"cases"`
	}
	b, err := os.ReadFile("../../testdata/parity/detector.json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(b, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Obfuscation != "xor-v1:telescreen-go-parity-fixture" || fixture.Meta.Generated < 2000 {
		t.Fatal("invalid fixture metadata")
	}
	tables, err := pytext.Default()
	if err != nil {
		t.Fatal(err)
	}
	detector := NewDetector(tables)
	key := []byte("telescreen-go-parity-fixture")
	for i, c := range fixture.Cases {
		raw, err := base64.StdEncoding.DecodeString(c.Text)
		if err != nil {
			t.Fatalf("case %d invalid encoding", i)
		}
		for j := range raw {
			raw[j] ^= key[j%len(key)]
		}
		got := detector.ScanText(string(raw))
		if len(got) != len(c.Findings) {
			t.Errorf("case %d: got rules %v, expected rules %v", i, ids(got), fixtureIDs(c.Findings))
			continue
		}
		for j, w := range c.Findings {
			if got[j] != (RawFinding{w.ID, w.Class, Span{w.Start, w.End}, w.Confidence}) {
				t.Errorf("case %d: got rules %v, expected rules %v", i, ids(got), fixtureIDs(c.Findings))
				break
			}
		}
	}
	t.Logf("checked %d cases (%d generated)", len(fixture.Cases), fixture.Meta.Generated)
}
func ids(fs []RawFinding) []string {
	out := []string{}
	for _, f := range fs {
		out = append(out, f.RuleID)
	}
	return out
}
func fixtureIDs(fs []struct {
	ID         string    `json:"rule_id"`
	Class      RuleClass `json:"rule_class"`
	Start      int       `json:"start"`
	End        int       `json:"end"`
	Confidence float64   `json:"confidence"`
}) []string {
	out := []string{}
	for _, f := range fs {
		out = append(out, f.ID)
	}
	return out
}
