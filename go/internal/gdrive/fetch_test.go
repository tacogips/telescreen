package gdrive

import (
	"context"
	"os"
	"testing"

	"github.com/tacogips/telescreen/go/internal/detect"
	"github.com/tacogips/telescreen/go/internal/gapi"
	"github.com/tacogips/telescreen/go/internal/pytext"
)

type fetchDriveFake struct {
	file                       gapi.DriveFile
	metaErr, permErr, mediaErr error
	data                       []byte
	oversize                   bool
}

func (d *fetchDriveFake) FilesList(context.Context, gapi.FilesListParams) (gapi.FileListPage, error) {
	return gapi.FileListPage{}, nil
}
func (d *fetchDriveFake) FilesGetMeta(context.Context, string, *string) (gapi.DriveFile, error) {
	return d.file, d.metaErr
}
func (d *fetchDriveFake) ListPermissions(context.Context, string, *string) ([]gapi.DrivePermission, error) {
	return nil, d.permErr
}
func (d *fetchDriveFake) Download(context.Context, string, *string, int) ([]byte, bool, error) {
	return d.data, d.oversize, d.mediaErr
}
func (d *fetchDriveFake) Export(context.Context, string, string, *string, int) ([]byte, bool, error) {
	return d.data, d.oversize, d.mediaErr
}
func (d *fetchDriveFake) DrivesList(context.Context, *string, string) (gapi.DrivesPage, error) {
	return gapi.DrivesPage{}, nil
}

type fetchSheetsFake struct {
	values []gapi.SheetValues
	err    error
}

func (s *fetchSheetsFake) SheetTitles(context.Context, string, *string) ([]string, error) {
	return []string{"sheet"}, s.err
}
func (s *fetchSheetsFake) ValuesBatchGet(context.Context, string, []string, *string) ([]gapi.SheetValues, error) {
	return s.values, s.err
}

type fetchEngineFake struct{ fail, hit bool }

func (e fetchEngineFake) HasHits(string) bool                                 { return e.hit }
func (e fetchEngineFake) ScanSegment(string, string) ([]detect.Finding, bool) { return nil, !e.fail }

func TestFetchOutcomes(t *testing.T) {
	tables, err := pytext.Load(os.DirFS("../pytext/data"))
	if err != nil {
		t.Fatal(err)
	}
	delay := 4.0
	cases := []struct {
		name     string
		drive    fetchDriveFake
		engine   fetchEngineFake
		kind     string
		want     fetchState
		exposure bool
	}{
		{name: "gone metadata", drive: fetchDriveFake{metaErr: gapi.GoneError{}}, want: fetchRemoved},
		{name: "trashed", drive: fetchDriveFake{file: gapi.DriveFile{Trashed: true}}, want: fetchRemoved},
		{name: "metadata failure", drive: fetchDriveFake{metaErr: gapi.FetchFailedError{}}, want: fetchFailed},
		{name: "nonmatching", kind: "gdocs", drive: fetchDriveFake{file: gapi.DriveFile{MimeType: "text/plain"}}, want: fetchFailed},
		{name: "permission gone", drive: fetchDriveFake{file: gapi.DriveFile{MimeType: "text/plain"}, permErr: gapi.GoneError{}}, want: fetchRemoved},
		{name: "permission error", drive: fetchDriveFake{file: gapi.DriveFile{MimeType: "text/plain"}, permErr: gapi.FetchFailedError{}, data: []byte("clean")}, want: fetchComplete},
		{name: "image", drive: fetchDriveFake{file: gapi.DriveFile{MimeType: "image/png"}}, want: fetchComplete, exposure: true},
		{name: "oversized", drive: fetchDriveFake{file: gapi.DriveFile{MimeType: "text/plain"}, oversize: true}, want: fetchFailed},
		{name: "invalid utf8", drive: fetchDriveFake{file: gapi.DriveFile{MimeType: "text/plain"}, data: []byte{0xff}}, want: fetchFailed},
		{name: "export gone", kind: "gdocs", drive: fetchDriveFake{file: gapi.DriveFile{MimeType: "application/vnd.google-apps.document"}, mediaErr: gapi.GoneError{}}, want: fetchRemoved},
		{name: "410 failure", drive: fetchDriveFake{file: gapi.DriveFile{MimeType: "text/plain"}, mediaErr: gapi.FetchFailedError{}}, want: fetchFailed},
		{name: "engine failure", drive: fetchDriveFake{file: gapi.DriveFile{MimeType: "text/plain"}, data: []byte("clean")}, engine: fetchEngineFake{fail: true}, want: fetchFailed},
		{name: "locator hit", engine: fetchEngineFake{hit: true}, want: fetchFailed},
		{name: "rate limit", drive: fetchDriveFake{metaErr: gapi.RateLimitedError{RetryAfterSeconds: &delay}}, want: fetchRateLimited},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			kind := c.kind
			if kind == "" {
				kind = "gdrive"
			}
			service := NewService(Config{MaxBytes: 20}, &c.drive, &fetchSheetsFake{}, nil, c.engine, tables, nil)
			got := service.fetch(context.Background(), selector{Kind: kind}, "file", nil)
			if got.state != c.want {
				t.Fatalf("state=%v want=%v", got.state, c.want)
			}
			if (got.exposure != nil) != c.exposure {
				t.Fatalf("exposure present=%v", got.exposure != nil)
			}
			if got.state == fetchRateLimited && (got.retryAfter == nil || *got.retryAfter != delay) {
				t.Fatal("rate limit delay missing")
			}
		})
	}
}

func TestFetchSheetsInventory(t *testing.T) {
	tables, err := pytext.Load(os.DirFS("../pytext/data"))
	if err != nil {
		t.Fatal(err)
	}
	drive := &fetchDriveFake{file: gapi.DriveFile{MimeType: "application/vnd.google-apps.spreadsheet"}}
	sheets := &fetchSheetsFake{values: []gapi.SheetValues{{Range: "'sheet'!A:ZZZ", Values: [][]gapi.CellValue{{{Kind: gapi.CellString, Text: "x"}}, {{Kind: gapi.CellString, Text: ""}}}}}}
	service := NewService(Config{MaxBytes: 20}, drive, sheets, nil, fetchEngineFake{}, tables, nil)
	got := service.fetch(context.Background(), selector{Kind: "gsheets"}, "file", nil)
	if got.state != fetchComplete || got.bytes != 1 || got.inventory == nil || got.inventory.Kind != "table" || got.inventory.ItemCount == nil || *got.inventory.ItemCount != 1 {
		t.Fatalf("unexpected outcome: %+v", got)
	}
}
