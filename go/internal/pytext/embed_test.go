package pytext

import (
	"github.com/tacogips/telescreen/go/internal/unorm"
	"testing"
)

func TestDefaultUnicodeVersion(t *testing.T) {
	tables, err := Default()
	if err != nil {
		t.Fatal(err)
	}
	if tables.UnidataVersion() != unorm.Version() {
		t.Fatalf("Unicode version mismatch: Python %s, normalization %s", tables.UnidataVersion(), unorm.Version())
	}
	if tables.PythonVersion() == "" {
		t.Fatal("missing Python version")
	}
}
