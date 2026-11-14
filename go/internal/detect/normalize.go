package detect

import "github.com/tacogips/telescreen/go/internal/unorm"

func normalize(text []rune) ([]rune, []int) {
	var normalized []rune
	var indexes []int
	for i, r := range text {
		for _, expanded := range unorm.NFKCRune(r) {
			normalized = append(normalized, expanded)
			indexes = append(indexes, i)
		}
	}
	indexes = append(indexes, len(text))
	return normalized, indexes
}

func toOriginal(span Span, indexes []int) Span {
	previous := indexes[span.End-1]
	end := indexes[span.End]
	if end == previous {
		end = previous + 1
	}
	return Span{Start: indexes[span.Start], End: end}
}
