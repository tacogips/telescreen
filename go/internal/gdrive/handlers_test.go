package gdrive

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"testing"

	"github.com/tacogips/telescreen/go/internal/detect"
	"github.com/tacogips/telescreen/go/internal/gapi"
	"github.com/tacogips/telescreen/go/internal/police"
	"github.com/tacogips/telescreen/go/internal/pytext"
)

type handlerEngine struct{ snippet string }

func (e handlerEngine) ScanSegment(string, string) ([]detect.Finding, bool) {
	return []detect.Finding{{RuleID: "rule", RuleClass: detect.RuleToken, MaskedSnippet: e.snippet, Location: "L1", ExternalFingerprint: "abc"}}, true
}
func (handlerEngine) HasHits(string) bool { return false }

type handlerDrive struct{ partDrive }

func (d *handlerDrive) FilesGetMeta(_ context.Context, id string, _ *string) (gapi.DriveFile, error) {
	if id == "bad" {
		return gapi.DriveFile{}, gapi.FetchFailedError{}
	}
	file := gapi.DriveFile{ID: id, MimeType: "text/plain"}
	if strings.HasPrefix(id, "f") {
		drive := "d"
		file.DriveID = &drive
	}
	return file, nil
}
func (*handlerDrive) Download(context.Context, string, *string, int) ([]byte, bool, error) {
	return []byte("sample"), false, nil
}
func handlerTask(input string) police.TaskEnvelope {
	return police.TaskEnvelope{TaskID: "task", Input: json.RawMessage(input)}
}
func handlerService(t *testing.T, engine detect.Engine) *Service {
	t.Helper()
	tables, err := pytext.Load(os.DirFS("../pytext/data"))
	if err != nil {
		t.Fatal(err)
	}
	return NewService(Config{MaxBytes: 100, ShardSize: 100}, &handlerDrive{}, &fetchSheetsFake{}, nil, engine, tables, slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil)))
}
func TestHandlerValidation(t *testing.T) {
	s := handlerService(t, handlerEngine{})
	for _, v := range []string{`{"selector":{"kind":"gdrive","extra":1},"descriptor":{"corpus":"drive:d","file_ids":[]}}`, `{"selector":{},"descriptor":{"corpus":"drive:d","file_ids":[]}}`, `{"selector":{"kind":1},"descriptor":{"corpus":"drive:d","file_ids":[]}}`} {
		r := s.scanShard(context.Background(), handlerTask(v))
		if r.Error == nil || r.Error.Code != police.CodeInvalidSelector {
			t.Fatalf("selector: %+v", r)
		}
	}
	r := s.scanShard(context.Background(), handlerTask(`{"selector":{"kind":"gdrive"},"descriptor":{"corpus":"drive:d","file_ids":[1]}}`))
	if r.Error == nil || r.Error.Code != police.CodeInvalidDescriptor {
		t.Fatalf("descriptor: %+v", r)
	}
	r = s.scanShard(context.Background(), handlerTask(`{"selector":{"kind":"gdrive"},"descriptor":{"corpus":"user:u","file_ids":["a","b"]}}`))
	if r.Stats == nil || r.Stats.ResourcesFailed != 2 {
		t.Fatalf("subject failure: %+v", r)
	}
}
func TestShardAccountingAndSizeGuard(t *testing.T) {
	s := handlerService(t, handlerEngine{snippet: strings.Repeat("x", 600000)})
	r := s.scanShard(context.Background(), handlerTask(`{"selector":{"kind":"gdrive"},"descriptor":{"corpus":"drive:d","file_ids":["a","bad","b"]}}`))
	encoded, err := police.EncodeResult(r)
	if err != nil || len(encoded) > 1048576 || r.Stats == nil || r.Stats.ResourcesFailed != 2 || len(r.CompleteLocators) != 1 || len(r.CompleteLocators)+r.Stats.ResourcesFailed != 3 {
		t.Fatalf("size or accounting: %+v size=%d", r.Stats, len(encoded))
	}
	if len(r.ResourceExposures) != 1 || len(r.ResourceInventories) != 1 {
		t.Fatal("complete resource must retain exposure and inventory")
	} // Design 8.1: Bureau discards finding-free entries on a failed shard.
}
func TestDetectLimitAndEmpty(t *testing.T) {
	s := handlerService(t, handlerEngine{})
	d := s.drive.(*handlerDrive)
	id := "d"
	for i := 0; i < 1005; i++ {
		d.files = append(d.files, gapi.DriveFile{ID: fmt.Sprintf("f%d", i), MimeType: "text/plain", DriveID: &id})
	}
	r := s.detect(context.Background(), handlerTask(`{"selector":{"kind":"gdrive","drive_ids":["d"]}}`))
	if !strings.Contains(r.Summary, "scanned 1000; not scanned 5") || len(r.Findings) != 1000 {
		t.Fatalf("summary %q", r.Summary)
	}
	r = s.detect(context.Background(), handlerTask(`{"selector":{"kind":"gdrive"}}`))
	if r.Status != police.StatusCompleted || len(r.Findings) != 0 {
		t.Fatalf("empty: %+v", r)
	}
}

type rateDrive struct{ handlerDrive }

func (*rateDrive) FilesGetMeta(_ context.Context, id string, _ *string) (gapi.DriveFile, error) {
	if id == "third" {
		v := 7.0
		return gapi.DriveFile{}, gapi.RateLimitedError{RetryAfterSeconds: &v}
	}
	return gapi.DriveFile{ID: id, MimeType: "text/plain", Name: "sensitive-name"}, nil
}
func TestRateLimitAndLogLeak(t *testing.T) {
	s := handlerService(t, handlerEngine{})
	s.drive = &rateDrive{}
	var log bytes.Buffer
	s.logger = slog.New(slog.NewTextHandler(&log, nil))
	r := s.scanShard(context.Background(), handlerTask(`{"selector":{"kind":"gdrive"},"descriptor":{"corpus":"drive:d","file_ids":["first","second","third"]}}`))
	if r.Error == nil || r.Error.Code != police.CodeRateLimited || r.Error.RetryAfterSeconds == nil || *r.Error.RetryAfterSeconds != 7 || len(r.Findings) != 0 {
		t.Fatalf("rate: %+v", r)
	}
	for _, sensitive := range []string{"first", "second", "third", "sensitive-name", "sample", "person" + "@example.com"} {
		if strings.Contains(log.String(), sensitive) {
			t.Fatal("sensitive data in log")
		}
	}
}
func TestNullInputRejected(t *testing.T) {
	s := handlerService(t, handlerEngine{})
	for _, input := range []string{`{"selector":{"kind":"gdrive","drive_ids":null},"descriptor":{"corpus":"drive:d","file_ids":[]}}`, `{"selector":{"kind":"gdrive","drive_ids":[null]},"descriptor":{"corpus":"drive:d","file_ids":[]}}`} {
		r := s.scanShard(context.Background(), handlerTask(input))
		if r.Error == nil || r.Error.Code != police.CodeInvalidSelector {
			t.Fatal("invalid selector accepted")
		}
	}
	r := s.scanShard(context.Background(), handlerTask(`{"selector":{"kind":"gdrive"},"descriptor":{"corpus":"drive:d","file_ids":[null]}}`))
	if r.Error == nil || r.Error.Code != police.CodeInvalidDescriptor {
		t.Fatal("invalid descriptor accepted")
	}
}

type mixedDrive struct{ handlerDrive }

func (*mixedDrive) Download(_ context.Context, id string, _ *string, _ int) ([]byte, bool, error) {
	if id == "clean" {
		return []byte("ordinary"), false, nil
	}
	return []byte("private" + "-content person" + "@example.com"), false, nil
}

type mixedEngine struct{}

func (mixedEngine) HasHits(string) bool { return false }
func (mixedEngine) ScanSegment(text, _ string) ([]detect.Finding, bool) {
	if text == "ordinary" {
		return nil, true
	}
	return []detect.Finding{{RuleID: "rule", RuleClass: detect.RuleToken, MaskedSnippet: "[MASKED]", Location: "L1", ExternalFingerprint: "abc"}}, true
}
func TestMixedShardAndContentLog(t *testing.T) {
	s := handlerService(t, mixedEngine{})
	s.drive = &mixedDrive{}
	var logs bytes.Buffer
	s.logger = slog.New(slog.NewTextHandler(&logs, nil))
	r := s.scanShard(context.Background(), handlerTask(`{"selector":{"kind":"gdrive"},"descriptor":{"corpus":"drive:d","file_ids":["bad","finding","clean"]}}`))
	if r.Stats == nil || r.Stats.ResourcesFailed != 1 || len(r.CompleteLocators) != 2 || len(r.Findings) != 1 || len(r.ResourceExposures) != 2 || len(r.ResourceInventories) != 2 {
		t.Fatalf("mixed shard: %+v", r)
	} // Design 8.1: Bureau discards finding-free entries on a failed shard.
	for _, value := range []string{"private" + "-content", "person" + "@example.com", "finding", "clean"} {
		if strings.Contains(logs.String(), value) {
			t.Fatal("sensitive data in log")
		}
	}
}

type failingAdmin struct{ partAdmin }

func (failingAdmin) UserEmail(context.Context, string) (string, error) {
	return "", gapi.FetchFailedError{}
}
func TestSubjectLookupFailure(t *testing.T) {
	s := handlerService(t, handlerEngine{})
	s.admin = failingAdmin{}
	r := s.scanShard(context.Background(), handlerTask(`{"selector":{"kind":"gdrive"},"descriptor":{"corpus":"user:u","file_ids":["a","b"]}}`))
	if r.Status != police.StatusCompleted || r.Stats == nil || r.Stats.ResourcesFailed != 2 || len(r.CompleteLocators) != 0 {
		t.Fatalf("subject lookup failure: %+v", r)
	}
}
func TestSizeGuardHeaviest(t *testing.T) {
	s := handlerService(t, handlerEngine{})
	large := strings.Repeat("x", 400000)
	finding := police.MaskedFinding{Locator: "gdrive:a", RuleID: "rule", RuleClass: "token", MaskedSnippet: large, Location: "L1", ExternalFingerprint: "abc"}
	outcomes := []fetchOutcome{{locator: "gdrive:a", findings: []police.MaskedFinding{finding}}, {locator: "gdrive:b", findings: []police.MaskedFinding{finding, finding}}}
	r := s.assemble(handlerTask(`{}`), outcomes, 0, true, "")
	if r.Stats == nil || r.Stats.ResourcesFailed != 1 || len(r.CompleteLocators) != 1 || r.CompleteLocators[0] != "gdrive:a" {
		t.Fatal("heaviest resource was not removed")
	}
}
