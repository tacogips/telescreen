package pytext

import (
	"embed"
	"io/fs"
)

// dataFS is the module's only package-level variable and is read-only.
//
//go:embed data/*.txt
var dataFS embed.FS

func Default() (*Tables, error) {
	data, err := fs.Sub(dataFS, "data")
	if err != nil {
		return nil, err
	}
	return Load(data)
}
