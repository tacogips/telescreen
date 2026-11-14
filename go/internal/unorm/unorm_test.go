package unorm

import "testing"

func TestNormalization(t *testing.T) { // Design section 9.2 pins Python 3.12 Unicode data.
	if Version() != "15.0.0" || NFKCRune('Ａ') != "A" || NFKC("e\u0301") != "é" {
		t.Fatal("normalization parity failed", Version())
	}
}
