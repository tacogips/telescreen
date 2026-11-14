package gdrive

import (
	"math/big"
	"strconv"
	"strings"

	"github.com/tacogips/telescreen/go/internal/gapi"
	"github.com/tacogips/telescreen/go/internal/pytext"
)

type segment struct{ Locator, Text string }

func lineSegments(text string, tables *pytext.Tables) []segment {
	lines := tables.SplitLines(text)
	out := make([]segment, 0, len(lines))
	for i, line := range lines {
		out = append(out, segment{Locator: "L" + strconv.Itoa(i+1), Text: line})
	}
	return out
}

func sheetRanges(titles []string) []string {
	out := make([]string, 0, len(titles))
	for _, title := range titles {
		out = append(out, "'"+strings.ReplaceAll(title, "'", "''")+"'!A:ZZZ")
	}
	return out
}

func cellText(cell gapi.CellValue) string {
	switch cell.Kind {
	case gapi.CellString:
		return cell.Text
	case gapi.CellBool:
		if cell.Bool {
			return "True"
		}
		return "False"
	case gapi.CellNumber:
		if !strings.ContainsAny(cell.Number, ".eE") {
			value, ok := new(big.Int).SetString(cell.Number, 10)
			if ok {
				return value.String()
			}
			return ""
		}
		value, err := strconv.ParseFloat(cell.Number, 64)
		if err != nil {
			return ""
		}
		sci := strconv.FormatFloat(value, 'e', -1, 64)
		parts := strings.SplitN(sci, "e", 2)
		exponent, err := strconv.Atoi(parts[1])
		if err != nil {
			return ""
		}
		if exponent >= -4 && exponent < 16 {
			fixed := strconv.FormatFloat(value, 'f', -1, 64)
			if !strings.Contains(fixed, ".") {
				fixed += ".0"
			}
			return fixed
		}
		return sci
	}
	return ""
}

func sheetSegments(values []gapi.SheetValues) ([]segment, int) {
	out := []segment{}
	rows := 0
	for _, sheet := range values {
		name := strings.Trim(strings.SplitN(sheet.Range, "!", 2)[0], "'")
		for i, row := range sheet.Values {
			nonempty := false
			for j, cell := range row {
				text := cellText(cell)
				if text == "" {
					continue
				}
				nonempty = true
				out = append(out, segment{Locator: name + "!R" + strconv.Itoa(i+1) + "C" + strconv.Itoa(j+1), Text: text})
			}
			if nonempty {
				rows++
			}
		}
	}
	return out, rows
}
