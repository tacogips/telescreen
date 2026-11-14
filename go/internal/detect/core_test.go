package detect

import (
	"encoding/base64"
	"math"
	"reflect"
	"strings"
	"testing"

	"github.com/tacogips/telescreen/go/internal/pytext"
)

func TestNormalizeAndOriginal(t *testing.T) {
	got, indexes := normalize([]rune("Ａ１ﬃ"))
	if string(got) != "A1ffi" || !reflect.DeepEqual(indexes, []int{0, 1, 2, 2, 2, 3}) {
		t.Fatalf("unexpected normalization: %q %v", string(got), indexes)
	}
	if got := toOriginal(Span{Start: 3, End: 4}, indexes); got != (Span{Start: 2, End: 3}) {
		t.Fatalf("unexpected source span: %v", got)
	}
}

func TestValidators(t *testing.T) {
	tables, err := pytext.Default()
	if err != nil {
		t.Fatal(err)
	}
	card := "4" + strings.Repeat("1", 15)
	invalidCard := "4" + strings.Repeat("1", 14) + "2"
	if !luhnValid([]rune(card), tables) || luhnValid([]rune(invalidCard), tables) || !knownCardIIN([]rune(card), tables) || knownCardIIN([]rune(strings.Repeat("9", 16)), tables) {
		t.Fatal("card validation")
	}
	if !luhnValid([]rune("٤"+strings.Repeat("١", 15)), tables) || !knownCardIIN([]rune("٥١"), tables) {
		t.Fatal("Unicode decimal validation")
	}
	if !myNumberValid([]rune("1234"+"5678"+"9018"), tables) || myNumberValid([]rune("1234"+"5678"+"9019"), tables) {
		t.Fatal("my number validation")
	}
	if math.Abs(shannonEntropy([]rune("aabb"))-1) > 1e-12 || shannonEntropy(nil) != 0 {
		t.Fatal("entropy")
	}
	header := base64.URLEncoding.EncodeToString([]byte(`{"alg":NaN}`))
	if !jwtHeaderValid([]rune(header+".x.y")) || jwtHeaderValid([]rune("e30=.x.y")) {
		t.Fatal("JWT header")
	}
	if !isPlaceholder([]rune("  CHANGEme "), tables) || isPlaceholder([]rune("something"), tables) {
		t.Fatal("placeholder")
	}
}

func TestDetectorCardSeparators(t *testing.T) {
	tables, err := pytext.Default()
	if err != nil {
		t.Fatal(err)
	}
	card := "4" + strings.Repeat("1", 3) + " " + strings.Repeat("1", 4) + "-" + strings.Repeat("1", 4) + " " + strings.Repeat("1", 4)
	findings := NewDetector(tables).ScanText(card)
	if len(findings) != 1 || findings[0].RuleID != "credit_card" || findings[0].RuleClass != RulePaymentCard || findings[0].Span != (Span{Start: 0, End: len([]rune(card))}) {
		t.Fatalf("unexpected card findings: %+v", findings)
	}
}
