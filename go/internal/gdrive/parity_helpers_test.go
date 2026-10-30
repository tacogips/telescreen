package gdrive

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"github.com/tacogips/telescreen/go/internal/gapi"
	"github.com/tacogips/telescreen/go/internal/police"
	"github.com/tacogips/telescreen/go/internal/pytext"
)

func fixtureTables(t *testing.T) *pytext.Tables {
	t.Helper()
	v, err := pytext.Load(os.DirFS("../pytext/data"))
	if err != nil {
		t.Fatal(err)
	}
	return v
}
func fixtureRead(t *testing.T, name string, dst interface{}) {
	t.Helper()
	b, err := os.ReadFile("../../testdata/parity/" + name + ".json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(b, dst); err != nil {
		t.Fatal(err)
	}
}
func fixtureDecode(t *testing.T, s string) string {
	t.Helper()
	b, err := base64.StdEncoding.DecodeString(s)
	if err != nil {
		t.Fatal(err)
	}
	key := []byte("telescreen-go-parity-fixture")
	for i := range b {
		b[i] ^= key[i%len(key)]
	}
	return string(b)
}
func TestExposureParity(t *testing.T) {
	var fixture struct {
		Cases []struct {
			Name        string   `json:"name"`
			DriveID     *string  `json:"drive_id"`
			Domains     []string `json:"internal_domains"`
			Permissions []struct {
				Type, Role string
				Domain     *string
				Email      *string `json:"email_address"`
				Discovery  bool    `json:"allow_file_discovery"`
			} `json:"permissions"`
			Expected police.ResourceExposure `json:"expected"`
		} `json:"cases"`
	}
	fixtureRead(t, "exposure", &fixture)
	if len(fixture.Cases) == 0 {
		t.Fatal("empty fixture")
	}
	tables := fixtureTables(t)
	for _, c := range fixture.Cases {
		p := []gapi.DrivePermission{}
		for _, x := range c.Permissions {
			p = append(p, gapi.DrivePermission{Type: x.Type, Role: x.Role, Domain: x.Domain, EmailAddress: x.Email, AllowFileDiscovery: x.Discovery})
		}
		got := driveExposure(p, c.DriveID, c.Domains, tables)
		if got != c.Expected {
			t.Errorf("%s: got %+v expected %+v", c.Name, got, c.Expected)
		}
	}
}
func TestMatchingParity(t *testing.T) {
	var fixture struct {
		Cases []struct {
			Corpus string `json:"corpus_key"`
			Kind   string `json:"kind"`
			File   struct {
				ID      string   `json:"id"`
				Mime    string   `json:"mime_type"`
				DriveID *string  `json:"drive_id"`
				Parents []string `json:"parents"`
				Trashed bool     `json:"trashed"`
			} `json:"file"`
			Selector struct {
				DriveIDs  []string `json:"drive_ids"`
				FolderIDs []string `json:"folder_ids"`
				MimeTypes []string `json:"mime_types"`
			} `json:"selector"`
			Matches  bool `json:"matches"`
			Included bool `json:"included"`
		} `json:"cases"`
	}
	fixtureRead(t, "matching", &fixture)
	if len(fixture.Cases) == 0 {
		t.Fatal("empty fixture")
	}
	for i, c := range fixture.Cases {
		f := gapi.DriveFile{ID: c.File.ID, MimeType: c.File.Mime, DriveID: c.File.DriveID, Parents: c.File.Parents, Trashed: c.File.Trashed}
		s := c.Selector
		if got := matchFile(f, c.Kind, s.DriveIDs, s.FolderIDs, s.MimeTypes); got != c.Matches {
			t.Errorf("case %d: matches %v expected %v", i, got, c.Matches)
		}
		if got := included(f, c.Corpus, s.DriveIDs, s.FolderIDs); got != c.Included {
			t.Errorf("case %d: included %v expected %v", i, got, c.Included)
		}
	}
}
func TestSegmentsParity(t *testing.T) {
	type expectedSegment struct {
		Locator string `json:"locator"`
		Text    string `json:"text_b64"`
	}
	var fixture struct {
		Obfuscation string `json:"obfuscation"`
		Lines       []struct {
			Text     string            `json:"text_b64"`
			Segments []expectedSegment `json:"segments"`
		} `json:"lines"`
		Sheets []struct {
			Titles   []string           `json:"titles"`
			Ranges   []string           `json:"ranges"`
			Values   []gapi.SheetValues `json:"value_ranges"`
			Segments []expectedSegment  `json:"segments"`
			Count    int                `json:"item_count"`
		} `json:"sheets"`
	}
	fixtureRead(t, "segments", &fixture)
	if fixture.Obfuscation != "xor-v1:telescreen-go-parity-fixture" || len(fixture.Lines) == 0 || len(fixture.Sheets) == 0 {
		t.Fatal("invalid or empty fixture")
	}
	tables := fixtureTables(t)
	convert := func(items []expectedSegment) []segment {
		out := []segment{}
		for _, s := range items {
			out = append(out, segment{Locator: s.Locator, Text: fixtureDecode(t, s.Text)})
		}
		return out
	}
	for i, c := range fixture.Lines {
		if got, want := lineSegments(fixtureDecode(t, c.Text), tables), convert(c.Segments); !reflect.DeepEqual(got, want) {
			t.Errorf("line %d: %+v != %+v", i, got, want)
		}
	}
	for i, c := range fixture.Sheets {
		if got := sheetRanges(c.Titles); !reflect.DeepEqual(got, c.Ranges) {
			t.Errorf("ranges %d: %v != %v", i, got, c.Ranges)
		}
		got, count := sheetSegments(c.Values)
		if want := convert(c.Segments); !reflect.DeepEqual(got, want) || count != c.Count {
			t.Errorf("sheet %d: %+v %d != %+v %d", i, got, count, want, c.Count)
		}
	}
}
