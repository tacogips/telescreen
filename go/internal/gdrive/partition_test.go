package gdrive

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/tacogips/telescreen/go/internal/detect"
	"github.com/tacogips/telescreen/go/internal/gapi"
	"github.com/tacogips/telescreen/go/internal/police"
)

type partDrive struct {
	files  []gapi.DriveFile
	params gapi.FilesListParams
}

func (d *partDrive) FilesList(_ context.Context, p gapi.FilesListParams) (gapi.FileListPage, error) {
	d.params = p
	return gapi.FileListPage{Files: d.files}, nil
}
func (*partDrive) FilesGetMeta(context.Context, string, *string) (gapi.DriveFile, error) {
	return gapi.DriveFile{}, nil
}
func (*partDrive) ListPermissions(context.Context, string, *string) ([]gapi.DrivePermission, error) {
	return nil, nil
}
func (*partDrive) Download(context.Context, string, *string, int) ([]byte, bool, error) {
	return nil, false, nil
}
func (*partDrive) Export(context.Context, string, string, *string, int) ([]byte, bool, error) {
	return nil, false, nil
}
func (*partDrive) DrivesList(_ context.Context, token *string, _ string) (gapi.DrivesPage, error) {
	if token == nil {
		next := "two"
		return gapi.DrivesPage{Drives: []gapi.DriveRef{{ID: "one"}}, NextPageToken: &next}, nil
	}
	return gapi.DrivesPage{Drives: []gapi.DriveRef{{ID: "two"}}}, nil
}

type partAdmin struct{}

func (partAdmin) UsersList(_ context.Context, token *string) (gapi.UsersPage, error) {
	if token == nil {
		next := "two"
		return gapi.UsersPage{Users: []gapi.DirectoryUser{{ID: "one"}}, NextPageToken: &next}, nil
	}
	return gapi.UsersPage{Users: []gapi.DirectoryUser{{ID: "two"}}}, nil
}
func (partAdmin) UserEmail(context.Context, string) (string, error) { return "subject", nil }
func (partAdmin) AdminSubject() string                              { return "subject" }

type partEngine struct{ hit string }

func (partEngine) ScanSegment(string, string) ([]detect.Finding, bool) { return nil, true }
func (e partEngine) HasHits(s string) bool                             { return e.hit != "" && s == e.hit }
func partTask(corpus string, cursor *string) police.TaskEnvelope {
	b, _ := json.Marshal(struct {
		Selector selector `json:"selector"`
		Corpus   string   `json:"corpus_key"`
		Cursor   *string  `json:"cursor"`
	}{selector{Kind: "gdrive", DriveIDs: []string{"d"}, FolderIDs: []string{"f"}}, corpus, cursor})
	return police.TaskEnvelope{TaskID: "task", Input: b}
}
func TestPartitionRoots(t *testing.T) {
	d := &partDrive{}
	s := Service{drive: d, engine: partEngine{}}
	r := s.partition(context.Background(), partTask("", nil))
	if r.Payload == nil || len(r.Payload.SubCorpora) != 2 || r.Payload.NextCursor != nil {
		t.Fatalf("local root: %+v", r)
	}
	s.admin = partAdmin{}
	var cursor *string
	expected := []string{"users:two", "drives:", "drives:two", ""}
	for _, want := range expected {
		r = s.partition(context.Background(), partTask("", cursor))
		if r.Payload == nil || len(r.Payload.SubCorpora) != 1 {
			t.Fatalf("delegated root: %+v", r)
		}
		cursor = r.Payload.NextCursor
		got := ""
		if cursor != nil {
			got = *cursor
		}
		if got != want {
			t.Fatalf("cursor %q want %q", got, want)
		}
	}
	s.engine = partEngine{hit: "users:two"}
	r = s.partition(context.Background(), partTask("", nil))
	if r.Error == nil || r.Error.Code != police.CodeRawValueGuard {
		t.Fatalf("guard: %+v", r)
	}
}
func TestPartitionCorpusAndErrors(t *testing.T) {
	d := &partDrive{}
	id := "d"
	for i := 0; i < 250; i++ {
		d.files = append(d.files, gapi.DriveFile{ID: fmt.Sprintf("file%03d", i), DriveID: &id, MimeType: "text/plain"})
	}
	s := Service{cfg: Config{ShardSize: 100}, drive: d, engine: partEngine{}}
	r := s.partition(context.Background(), partTask("drive:d", nil))
	if r.Payload == nil || len(r.Payload.Shards) != 3 || r.Payload.Shards[2].Weight != 50 || d.params.PageSize != 1000 {
		t.Fatalf("shards: %+v", r)
	}
	for _, tc := range []struct{ corpus, code string }{{"user:x", police.CodeSweepUnsupported}, {"bogus:x", police.CodeInvalidCorpus}} {
		r = s.partition(context.Background(), partTask(tc.corpus, nil))
		if r.Error == nil || r.Error.Code != tc.code {
			t.Fatalf("%s: %+v", tc.corpus, r)
		}
	}
}
