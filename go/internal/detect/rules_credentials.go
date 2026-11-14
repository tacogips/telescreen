package detect

import "github.com/tacogips/telescreen/go/internal/pytext"

func credentialBoundary(s []rune, i int, t *pytext.Tables) bool {
	left := i > 0 && t.IsWord(s[i-1])
	right := i < len(s) && t.IsWord(s[i])
	return left != right
}

func credentialLiteral(s []rune, i int, literal string) (int, bool) {
	for _, r := range literal {
		if i >= len(s) || s[i] != r {
			return i, false
		}
		i++
	}
	return i, true
}
func credentialAlphaNum(r rune) bool {
	return r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z' || r >= '0' && r <= '9'
}
func credentialUpperNum(r rune) bool { return r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' }
func credentialToken(r rune) bool    { return credentialAlphaNum(r) || r == '_' || r == '-' }

func matchAwsAccessKeyID(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	if !credentialBoundary(s, start, t) {
		return 0, 0, 0, false
	}
	i, ok := credentialLiteral(s, start, "AKIA")
	if !ok {
		i, ok = credentialLiteral(s, start, "ASIA")
	}
	if !ok || i+16 > len(s) {
		return 0, 0, 0, false
	}
	for _, r := range s[i : i+16] {
		if !credentialUpperNum(r) {
			return 0, 0, 0, false
		}
	}
	i += 16
	if !credentialBoundary(s, i, t) {
		return 0, 0, 0, false
	}
	return start, i, i, true
}
func matchAwsSecretAccessKey(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	if !credentialBoundary(s, start, t) || start+40 > len(s) {
		return 0, 0, 0, false
	}
	for _, r := range s[start : start+40] {
		if !credentialAlphaNum(r) && r != '/' && r != '+' && r != '=' {
			return 0, 0, 0, false
		}
	}
	end := start + 40
	if !credentialBoundary(s, end, t) {
		return 0, 0, 0, false
	}
	return start, end, end, true
}
func matchGithubToken(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	if !credentialBoundary(s, start, t) {
		return 0, 0, 0, false
	}
	if start+4 <= len(s) && s[start] == 'g' && s[start+1] == 'h' && (s[start+2] == 'p' || s[start+2] == 'o' || s[start+2] == 'u' || s[start+2] == 's' || s[start+2] == 'r') && s[start+3] == '_' {
		i := start + 4
		end := i
		for end < len(s) && end-i < 255 && credentialAlphaNum(s[end]) {
			end++
		}
		for end >= i+36 {
			if credentialBoundary(s, end, t) {
				return start, end, end, true
			}
			end--
		}
	}
	if i, ok := credentialLiteral(s, start, "github_pat_"); ok && i+82 <= len(s) {
		end := i + 82
		valid := true
		for _, r := range s[i:end] {
			if !credentialAlphaNum(r) && r != '_' {
				valid = false
				break
			}
		}
		if valid && credentialBoundary(s, end, t) {
			return start, end, end, true
		}
	}
	return 0, 0, 0, false
}
func matchSlackToken(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	if !credentialBoundary(s, start, t) {
		return 0, 0, 0, false
	}
	if start+5 <= len(s) && s[start] == 'x' && s[start+1] == 'o' && s[start+2] == 'x' && (s[start+3] == 'a' || s[start+3] == 'b' || s[start+3] == 'p' || s[start+3] == 'r' || s[start+3] == 's') && s[start+4] == '-' {
		i := start + 5
		end := i
		for end < len(s) && (credentialAlphaNum(s[end]) || s[end] == '-') {
			end++
		}
		if end >= i+10 {
			return start, end, end, true
		}
	}
	if i, ok := credentialLiteral(s, start, "https://hooks.slack.com/services/"); ok {
		end := i
		for end < len(s) && (credentialAlphaNum(s[end]) || s[end] == '/') {
			end++
		}
		if end > i {
			return start, end, end, true
		}
	}
	return 0, 0, 0, false
}
func matchGoogleAPIKey(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	if !credentialBoundary(s, start, t) {
		return 0, 0, 0, false
	}
	i, ok := credentialLiteral(s, start, "AIza")
	if !ok {
		return 0, 0, 0, false
	}
	end := i
	for end < len(s) && end-i < 35 && credentialToken(s[end]) {
		end++
	}
	for end >= i+28 {
		if credentialBoundary(s, end, t) {
			return start, end, end, true
		}
		end--
	}
	return 0, 0, 0, false
}
func credentialMarker(s []rune, start int, prefix string) (int, bool) {
	i, ok := credentialLiteral(s, start, prefix)
	if !ok {
		return 0, false
	}
	for _, word := range []string{"RSA ", "EC ", "OPENSSH ", "DSA ", "PGP ", "ENCRYPTED ", ""} {
		j, ok := credentialLiteral(s, i, word)
		if ok {
			if end, ok := credentialLiteral(s, j, "PRIVATE KEY-----"); ok {
				return end, true
			}
		}
	}
	return 0, false
}
func matchPrivateKey(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	i, ok := credentialMarker(s, start, "-----BEGIN ")
	if !ok {
		return 0, 0, 0, false
	}
	for j := i; j < len(s); j++ {
		if end, ok := credentialMarker(s, j, "-----END "); ok {
			return start, end, end, true
		}
	}
	return 0, 0, 0, false
}
func matchJWT(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	if !credentialBoundary(s, start, t) {
		return 0, 0, 0, false
	}
	i, ok := credentialLiteral(s, start, "eyJ")
	if !ok {
		return 0, 0, 0, false
	}
	for i < len(s) && credentialToken(s[i]) {
		i++
	}
	if i >= len(s) || s[i] != '.' {
		return 0, 0, 0, false
	}
	i++
	begin := i
	for i < len(s) && credentialToken(s[i]) {
		i++
	}
	if i == begin || i >= len(s) || s[i] != '.' {
		return 0, 0, 0, false
	}
	i++
	begin = i
	for i < len(s) && credentialToken(s[i]) {
		i++
	}
	for i > begin {
		if credentialBoundary(s, i, t) {
			return start, i, i, true
		}
		i--
	}
	return 0, 0, 0, false
}
func matchPasswordAssignment(s []rune, start int, t *pytext.Tables) (int, int, int, bool) {
	for _, word := range []string{"password", "passwd", "pwd", "secret", "pass", "パスワード"} {
		i := start
		match := true
		for _, r := range word {
			if i >= len(s) || (r >= 'a' && r <= 'z' && !t.FoldsTo(r, s[i])) || (!(r >= 'a' && r <= 'z') && r != s[i]) {
				match = false
				break
			}
			i++
		}
		if !match {
			continue
		}
		for i < len(s) && t.IsSpace(s[i]) {
			i++
		}
		if i >= len(s) || (s[i] != ':' && s[i] != '=') {
			continue
		}
		i++
		for i < len(s) && t.IsSpace(s[i]) {
			i++
		}
		begin := i
		for i < len(s) && !t.IsSpace(s[i]) && s[i] != ',' && s[i] != ';' {
			i++
		}
		if i-begin >= 8 {
			return begin, i, i, true
		}
	}
	return 0, 0, 0, false
}
