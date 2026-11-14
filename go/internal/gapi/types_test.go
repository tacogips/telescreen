package gapi

import (
	"encoding/json"
	"testing"
)

func TestCellValue(t *testing.T) {
	for _, tc := range []struct {
		input string
		kind  CellKind
		text  string
		b     bool
	}{{`"x"`, CellString, "x", false}, {`12`, CellNumber, "12", false}, {`1.5e3`, CellNumber, "1.5e3", false}, {`true`, CellBool, "", true}} {
		var c CellValue
		if e := json.Unmarshal([]byte(tc.input), &c); e != nil || c.Kind != tc.kind || c.Text+c.Number != tc.text || c.Bool != tc.b {
			t.Fatalf("%s: %+v %v", tc.input, c, e)
		}
	}
	var c CellValue
	if json.Unmarshal([]byte("null"), &c) == nil {
		t.Fatal("null accepted")
	}
}
