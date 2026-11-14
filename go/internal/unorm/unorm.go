package unorm

import "golang.org/x/text/unicode/norm"

func NFKC(s string) string   { return norm.NFKC.String(s) }
func NFKCRune(r rune) string { return norm.NFKC.String(string(r)) }
func Version() string        { return norm.Version }
