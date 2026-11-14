package pytext

import (
	"bufio"
	"fmt"
	"io/fs"
	"sort"
	"strconv"
	"strings"
)

type interval struct {
	lo, hi rune
	value  int
}
type Tables struct {
	python, unidata                        string
	word, digit, space, linebreak, decimal []interval
	lower, fold                            map[rune][]rune
	ignore                                 map[rune]map[rune]bool
}

func Load(fsys fs.FS) (*Tables, error) {
	t := &Tables{lower: map[rune][]rune{}, fold: map[rune][]rune{}, ignore: map[rune]map[rune]bool{}}
	files := []string{"word.txt", "digit.txt", "space.txt", "linebreak.txt", "decimal.txt", "lower.txt", "casefold.txt", "ignorecase.txt"}
	header := ""
	for _, name := range files {
		b, err := fs.ReadFile(fsys, name)
		if err != nil {
			return nil, err
		}
		s := bufio.NewScanner(strings.NewReader(string(b)))
		if !s.Scan() {
			return nil, fmt.Errorf("missing header")
		}
		h := s.Text()
		if header == "" {
			header = h
			if _, err = fmt.Sscanf(h, "# python=%s unidata=%s", &t.python, &t.unidata); err != nil {
				return nil, fmt.Errorf("invalid header")
			}
		} else if h != header {
			return nil, fmt.Errorf("mismatched headers")
		}
		for s.Scan() {
			line := s.Text()
			if line == "" {
				continue
			}
			switch name {
			case "word.txt", "digit.txt", "space.txt", "linebreak.txt", "decimal.txt":
				fields := strings.Fields(line)
				if len(fields) < 1 || len(fields) > 2 || (name == "decimal.txt") != (len(fields) == 2) {
					return nil, fmt.Errorf("invalid range")
				}
				pair := strings.Split(fields[0], "-")
				if len(pair) != 2 {
					return nil, fmt.Errorf("invalid range")
				}
				a, e := hexRune(pair[0])
				if e != nil {
					return nil, e
				}
				z, e := hexRune(pair[1])
				if e != nil || a > z {
					return nil, fmt.Errorf("invalid range")
				}
				v := 0
				if len(fields) == 2 {
					v, e = strconv.Atoi(fields[1])
					if e != nil {
						return nil, e
					}
				}
				var dest *[]interval
				switch name {
				case "word.txt":
					dest = &t.word
				case "digit.txt":
					dest = &t.digit
				case "space.txt":
					dest = &t.space
				case "linebreak.txt":
					dest = &t.linebreak
				default:
					dest = &t.decimal
				}
				if len(*dest) > 0 && (*dest)[len(*dest)-1].hi >= a {
					return nil, fmt.Errorf("overlapping ranges")
				}
				*dest = append(*dest, interval{a, z, v})
			default:
				parts := strings.Split(line, "=")
				if len(parts) != 2 {
					return nil, fmt.Errorf("invalid mapping")
				}
				var key rune
				if name == "ignorecase.txt" {
					rs := []rune(parts[0])
					if len(rs) != 1 || rs[0] < 'a' || rs[0] > 'z' {
						return nil, fmt.Errorf("invalid letter")
					}
					key = rs[0]
				} else {
					key, err = hexRune(parts[0])
					if err != nil {
						return nil, err
					}
				}
				vals := []rune{}
				for _, v := range strings.Fields(parts[1]) {
					r, e := hexRune(v)
					if e != nil {
						return nil, e
					}
					vals = append(vals, r)
				}
				if len(vals) == 0 {
					return nil, fmt.Errorf("empty mapping")
				}
				if name == "lower.txt" {
					t.lower[key] = vals
				} else if name == "casefold.txt" {
					t.fold[key] = vals
				} else {
					set := map[rune]bool{}
					for _, r := range vals {
						set[r] = true
					}
					t.ignore[key] = set
				}
			}
		}
		if err := s.Err(); err != nil {
			return nil, err
		}
	}
	return t, nil
}
func hexRune(s string) (rune, error) {
	v, e := strconv.ParseUint(s, 16, 32)
	if e != nil || v > 0x10ffff {
		return 0, fmt.Errorf("invalid code point")
	}
	return rune(v), nil
}
func lookup(a []interval, r rune) (interval, bool) {
	i := sort.Search(len(a), func(i int) bool { return a[i].hi >= r })
	if i < len(a) && a[i].lo <= r {
		return a[i], true
	}
	return interval{}, false
}
func (t *Tables) PythonVersion() string   { return t.python }
func (t *Tables) UnidataVersion() string  { return t.unidata }
func (t *Tables) IsWord(r rune) bool      { _, ok := lookup(t.word, r); return ok }
func (t *Tables) IsDigit(r rune) bool     { _, ok := lookup(t.digit, r); return ok }
func (t *Tables) IsSpace(r rune) bool     { _, ok := lookup(t.space, r); return ok }
func (t *Tables) IsLineBreak(r rune) bool { _, ok := lookup(t.linebreak, r); return ok }
func (t *Tables) DecimalValue(r rune) (int, bool) {
	v, ok := lookup(t.decimal, r)
	if !ok {
		return 0, false
	}
	return v.value + int(r-v.lo), true
}
func (t *Tables) LowerRune(r rune) []rune {
	if v, ok := t.lower[r]; ok {
		return append([]rune{}, v...)
	}
	return []rune{r}
}
func (t *Tables) Lower(s string) string {
	var b strings.Builder
	for _, r := range s {
		b.WriteString(string(t.LowerRune(r)))
	}
	return b.String()
}
func (t *Tables) CaseFold(s string) string {
	var b strings.Builder
	for _, r := range s {
		if v, ok := t.fold[r]; ok {
			b.WriteString(string(v))
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}
func (t *Tables) FoldsTo(letter, r rune) bool { return t.ignore[letter][r] }
func (t *Tables) SplitLines(s string) []string {
	out := []string{}
	start := 0
	rs := []rune(s)
	for i := 0; i < len(rs); i++ {
		if t.IsLineBreak(rs[i]) {
			out = append(out, string(rs[start:i]))
			if rs[i] == '\r' && i+1 < len(rs) && rs[i+1] == '\n' {
				i++
			}
			start = i + 1
		}
	}
	if start < len(rs) {
		out = append(out, string(rs[start:]))
	}
	return out
}
func (t *Tables) Strip(s string) string { return strings.TrimFunc(s, t.IsSpace) }
