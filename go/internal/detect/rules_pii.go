package detect

import (
	"github.com/tacogips/telescreen/go/internal/pytext"
	"strings"
)

func piiDigit(t *pytext.Tables, r rune) bool { _, ok := t.DecimalValue(r); return ok }
func piiBoundary(s []rune, i int, t *pytext.Tables) bool {
	left, right := false, false
	if i > 0 {
		left = t.IsWord(s[i-1])
	}
	if i < len(s) {
		right = t.IsWord(s[i])
	}
	return left != right
}
func piiASCII(r rune) bool {
	return r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9'
}
func piiLetter(r rune) bool                          { return r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' }
func piiResult(start, end int) (int, int, int, bool) { return start, end, end, true }
func piiFail() (int, int, int, bool)                 { return 0, 0, 0, false }

func matchCard(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	if !piiBoundary(s, start, t) {
		return piiFail()
	}
	var walk func(int, int) (int, bool)
	walk = func(p, n int) (int, bool) {
		if n < 18 && p < len(s) && piiDigit(t, s[p]) {
			q := p + 1
			if q < len(s) && (s[q] == ' ' || s[q] == '-') {
				if end, ok := walk(q+1, n+1); ok {
					return end, true
				}
			}
			if end, ok := walk(q, n+1); ok {
				return end, true
			}
		}
		if n >= 12 && p < len(s) && piiDigit(t, s[p]) && piiBoundary(s, p+1, t) {
			return p + 1, true
		}
		return 0, false
	}
	if end, ok := walk(start, 0); ok {
		return piiResult(start, end)
	}
	return piiFail()
}
func matchMyNumber(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	if start > 0 && piiDigit(t, s[start-1]) {
		return piiFail()
	}
	var walk func(int, int) (int, bool)
	walk = func(p, block int) (int, bool) {
		for k := 0; k < 4; k++ {
			if p >= len(s) || !piiDigit(t, s[p]) {
				return 0, false
			}
			p++
		}
		if block == 2 {
			if p < len(s) && piiDigit(t, s[p]) {
				return 0, false
			}
			return p, true
		}
		if p < len(s) && (s[p] == '-' || s[p] == ' ') {
			if e, ok := walk(p+1, block+1); ok {
				return e, true
			}
		}
		return walk(p, block+1)
	}
	if end, ok := walk(start, 0); ok {
		return piiResult(start, end)
	}
	return piiFail()
}
func matchEmail(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	if !piiBoundary(s, start, t) {
		return piiFail()
	}
	p := start
	for p < len(s) && p-start < 64 && (piiASCII(s[p]) || strings.ContainsRune(".!#$%&'*+/=?^_`{|}~-", s[p])) {
		p++
	}
	for p > start {
		if p < len(s) && s[p] == '@' {
			q := p + 1
			if q < len(s) && piiASCII(s[q]) {
				q++
				max := q
				for max < len(s) && max-q < 61 && (piiASCII(s[max]) || s[max] == '-') {
					max++
				}
				// The optional domain tail includes its own final alphanumeric.
				for x := max; x >= q; x-- {
					ends := []int{q}
					if x < len(s) && piiASCII(s[x]) {
						ends = []int{x + 1}
					}
					if x != q && len(ends) == 1 && ends[0] == q {
						continue
					}
					for _, e := range ends {
						if end, ok := piiEmailSuffix(s, e, t); ok {
							return piiResult(start, end)
						}
					}
				}
				if end, ok := piiEmailSuffix(s, q, t); ok {
					return piiResult(start, end)
				}
			}
		}
		p--
	}
	return piiFail()
}
func piiEmailSuffix(s []rune, p int, t *pytext.Tables) (int, bool) {
	if p >= len(s) || s[p] != '.' {
		return 0, false
	}
	q := p + 1
	for q < len(s) && q-(p+1) < 63 && piiLetter(s[q]) {
		q++
	}
	for e := q; e >= p+3; e-- {
		if end, ok := piiEmailSuffix(s, e, t); ok {
			return end, true
		}
		if piiBoundary(s, e, t) {
			return e, true
		}
	}
	return 0, false
}
func matchPhoneJP(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	if start >= len(s) || s[start] != '0' || start > 0 && piiDigit(t, s[start-1]) {
		return piiFail()
	}
	p := start + 1
	// First alternative: two greedy variable-length digit groups, separated by hyphens.
	for a := 4; a >= 1; a-- {
		x := p + a
		if !piiDigits(s, p, x, t) || x >= len(s) || s[x] != '-' {
			continue
		}
		for b := 4; b >= 1; b-- {
			y := x + 1 + b
			if !piiDigits(s, x+1, y, t) || y >= len(s) || s[y] != '-' {
				continue
			}
			end := y + 5
			if piiDigits(s, y+1, end, t) && piiNotDigit(s, end, t) {
				return piiResult(start, end)
			}
		}
	}
	for n := 10; n >= 9; n-- {
		end := p + n
		if piiDigits(s, p, end, t) && piiNotDigit(s, end, t) {
			return piiResult(start, end)
		}
	}
	if p+1 < len(s) && strings.ContainsRune("5789", s[p]) && s[p+1] == '0' {
		for _, a := range []bool{true, false} {
			x := p + 2
			if a {
				if x >= len(s) || s[x] != '-' {
					continue
				}
				x++
			}
			if !piiDigits(s, x, x+4, t) {
				continue
			}
			x += 4
			for _, b := range []bool{true, false} {
				y := x
				if b {
					if y >= len(s) || s[y] != '-' {
						continue
					}
					y++
				}
				end := y + 4
				if piiDigits(s, y, end, t) && piiNotDigit(s, end, t) {
					return piiResult(start, end)
				}
			}
		}
	}
	return piiFail()
}
func piiDigits(s []rune, a, b int, t *pytext.Tables) bool {
	if a < 0 || b > len(s) {
		return false
	}
	for ; a < b; a++ {
		if !piiDigit(t, s[a]) {
			return false
		}
	}
	return true
}
func piiNotDigit(s []rune, p int, t *pytext.Tables) bool { return p >= len(s) || !piiDigit(t, s[p]) }
func matchPhoneIntl(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	if start >= len(s) || s[start] != '+' || start > 0 && t.IsWord(s[start-1]) {
		return piiFail()
	}
	for n := 3; n >= 1; n-- {
		p := start + 1 + n
		if !piiDigits(s, start+1, p, t) {
			continue
		}
		var walk func(int, int) (int, bool)
		walk = func(q, k int) (int, bool) {
			if k < 14 {
				if q < len(s) && (s[q] == ' ' || s[q] == '-') && q+1 < len(s) && piiDigit(t, s[q+1]) {
					if e, ok := walk(q+2, k+1); ok {
						return e, true
					}
				}
				if q < len(s) && piiDigit(t, s[q]) {
					if e, ok := walk(q+1, k+1); ok {
						return e, true
					}
				}
			}
			if k >= 7 && piiNotDigit(s, q, t) {
				return q, true
			}
			return 0, false
		}
		if e, ok := walk(p, 0); ok {
			return piiResult(start, e)
		}
	}
	return piiFail()
}
func matchAddressEN(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	if !piiBoundary(s, start, t) {
		return piiFail()
	}
	max := start
	for max < len(s) && max-start < 6 && piiDigit(t, s[max]) {
		max++
	}
	names := []string{"street", "st", "road", "rd", "avenue", "ave", "boulevard", "blvd", "lane", "ln"}
	for d := max; d > start; d-- {
		p := d
		for p < len(s) && t.IsSpace(s[p]) {
			p++
		}
		for ws := p; ws > d; ws-- {
			end := ws
			for end < len(s) && end-ws < 50 && piiAddrChar(s[end], t) {
				end++
			}
			for e := end; e >= ws+2; e-- {
				if e >= len(s) || !t.IsSpace(s[e]) {
					continue
				}
				for _, name := range names {
					n := []rune(name)
					if e+1+len(n) > len(s) {
						continue
					}
					ok := true
					for i, r := range n {
						if !t.FoldsTo(r, s[e+1+i]) {
							ok = false
							break
						}
					}
					z := e + 1 + len(n)
					if ok && piiBoundary(s, z, t) {
						return piiResult(start, z)
					}
				}
			}
		}
	}
	return piiFail()
}
func piiAddrChar(r rune, t *pytext.Tables) bool {
	if r >= '0' && r <= '9' || strings.ContainsRune(".' -", r) {
		return true
	}
	for c := 'a'; c <= 'z'; c++ {
		if t.FoldsTo(c, r) {
			return true
		}
	}
	return false
}
func matchAddressJP(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	if start >= len(s) {
		return piiFail()
	}
	if s[start] == '〒' {
		p := start + 1
		if !piiDigits(s, p, p+3, t) || p+3 >= len(s) || s[p+3] != '-' || !piiDigits(s, p+4, p+8, t) {
			return piiFail()
		}
		p += 8
		for p < len(s) && p-(start+9) < 40 && s[p] != '\n' {
			p++
		}
		return piiResult(start, p)
	}
	prefixes := []string{"東京都", "北海道", "京都府", "大阪府"}
	positions := []int{}
	for _, v := range prefixes {
		r := []rune(v)
		if start+len(r) <= len(s) && string(s[start:start+len(r)]) == v {
			positions = append(positions, start+len(r))
		}
	}
	for n := 3; n >= 2; n-- {
		p := start + n
		if p < len(s) && s[p] == '県' {
			ok := true
			for _, r := range s[start:p] {
				if r < '一' || r > '龯' {
					ok = false
				}
			}
			if ok {
				positions = append(positions, p+1)
			}
		}
	}
	for _, p := range positions {
		q := p
		for q < len(s) && q-p < 12 && piiJapanese(s[q]) {
			q++
		}
		for e := q; e >= p+2; e-- {
			if e >= len(s) || !strings.ContainsRune("市区町村", s[e]) {
				continue
			}
			base := e + 1
			for offset := 0; offset <= 24 && base+offset <= len(s); offset++ {
				if offset > 0 && s[base+offset-1] == '\n' {
					break
				}
				x := base + offset
				max := x
				for max < len(s) && max-x < 4 && piiDigit(t, s[max]) {
					max++
				}
				for d := max; d > x; d-- {
					for _, suffix := range []string{"丁目", "番地"} {
						v := []rune(suffix)
						if d+len(v) <= len(s) && string(s[d:d+len(v)]) == suffix {
							return piiResult(start, d+len(v))
						}
					}
					if d < len(s) && (s[d] == '-' || s[d] == '－') {
						y := d + 1
						end := y
						for end < len(s) && end-y < 4 && piiDigit(t, s[end]) {
							end++
						}
						if end > y {
							if end < len(s) && (s[end] == '-' || s[end] == '－') {
								z := end + 1
								for z < len(s) && z-(end+1) < 4 && piiDigit(t, s[z]) {
									z++
								}
								if z > end+1 {
									return piiResult(start, z)
								}
							}
							return piiResult(start, end)
						}
					}
				}
			}
		}
	}
	return piiFail()
}
func piiJapanese(r rune) bool {
	return r >= '一' && r <= '龯' || r >= 'ぁ' && r <= 'ん' || r >= 'ァ' && r <= 'ヶ' || r == 'ー'
}
