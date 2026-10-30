package pytext

import (
	"os"
	"reflect"
	"testing"
	"testing/fstest"
)

func TestTables(t *testing.T) {
	v, e := Load(os.DirFS("testdata"))
	if e != nil {
		t.Fatal(e)
	}
	if !v.IsWord('0') || !v.IsWord('9') || v.IsWord(':') || !v.IsDigit('5') || !v.IsSpace(' ') || !v.IsLineBreak('\n') {
		t.Fatal("ranges")
	}
	n, ok := v.DecimalValue('9')
	if !ok || n != 9 {
		t.Fatal(n, ok)
	}
	if v.Lower("İ") != "i\u0307" || v.CaseFold("ß") != "ss" || !v.FoldsTo('a', 'A') || v.Strip(" x ") != "x" {
		t.Fatal("mapping")
	}
	if !reflect.DeepEqual(v.SplitLines("a\r\nb\rc\n"), []string{"a", "b", "c"}) || len(v.SplitLines("")) != 0 {
		t.Fatal("splitlines")
	}
}
func TestMismatchedHeaders(t *testing.T) {
	f := fstest.MapFS{}
	for _, n := range []string{"word", "digit", "space", "linebreak", "decimal", "lower", "casefold", "ignorecase"} {
		f[n+".txt"] = &fstest.MapFile{Data: []byte("# python=3.12 unidata=15.0.0\n")}
	}
	f["digit.txt"] = &fstest.MapFile{Data: []byte("# python=3.11 unidata=15.0.0\n")}
	if _, e := Load(f); e == nil {
		t.Fatal("mismatch accepted")
	}
}
