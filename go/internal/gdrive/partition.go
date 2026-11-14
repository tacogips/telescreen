package gdrive

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sort"
	"strings"

	"github.com/tacogips/telescreen/go/internal/gapi"
	"github.com/tacogips/telescreen/go/internal/police"
)

type partitionError struct {
	code  string
	retry *float64
}

func (e partitionError) Error() string { return e.code }
func partitionFailure(err error) partitionError {
	var limited gapi.RateLimitedError
	if errors.As(err, &limited) {
		return partitionError{code: police.CodeRateLimited, retry: limited.RetryAfterSeconds}
	}
	return partitionError{code: police.CodePartitionFailed}
}
func (s *Service) partition(ctx context.Context, task police.TaskEnvelope) police.ResultEnvelope {
	var input struct {
		Selector json.RawMessage `json:"selector"`
		Corpus   string          `json:"corpus_key"`
		Cursor   *string         `json:"cursor"`
	}
	if json.Unmarshal(task.Input, &input) != nil {
		return partitionFailed(task, partitionError{code: police.CodeInvalidSelector})
	}
	sel, ok := parseSelector(input.Selector)
	if !ok {
		return partitionFailed(task, partitionError{code: police.CodeInvalidSelector})
	}
	page, err := s.partitionPage(ctx, sel, input.Corpus, input.Cursor)
	if err != nil {
		var pe partitionError
		if !errors.As(err, &pe) {
			pe = partitionFailure(err)
		}
		return partitionFailed(task, pe)
	}
	if s.partitionGuard(page) {
		return partitionFailed(task, partitionError{code: police.CodeRawValueGuard})
	}
	result := police.ResultEnvelope{ContractVersion: police.ContractVersion, TaskID: task.TaskID, Status: police.StatusCompleted, Payload: &page}
	encoded, err := police.EncodeResult(result)
	var value interface{}
	if err != nil || json.Unmarshal(encoded, &value) != nil || s.hasHits(value) {
		return partitionFailed(task, partitionError{code: police.CodeRawValueGuard})
	}
	return result
}
func partitionFailed(task police.TaskEnvelope, err partitionError) police.ResultEnvelope {
	return police.ResultEnvelope{ContractVersion: police.ContractVersion, TaskID: task.TaskID, Status: police.StatusFailed, Error: &police.ResultError{Code: err.code, Detail: err.code, RetryAfterSeconds: err.retry}}
}
func (s *Service) partitionGuard(page police.PartitionResult) bool {
	if page.NextCursor != nil && s.engine.HasHits(*page.NextCursor) {
		return true
	}
	for _, corpus := range page.SubCorpora {
		if s.engine.HasHits(corpus.CorpusKey) {
			return true
		}
	}
	for _, shard := range page.Shards {
		if s.engine.HasHits(shard.ShardKey) || s.engine.HasHits(shard.Descriptor.Corpus) {
			return true
		}
		for _, id := range shard.Descriptor.FileIDs {
			if s.engine.HasHits(id) {
				return true
			}
		}
	}
	return false
}
func (s *Service) partitionPage(ctx context.Context, sel selector, corpus string, cursor *string) (police.PartitionResult, error) {
	page := police.PartitionResult{Shards: []police.PartitionShard{}, SubCorpora: []police.SubCorpus{}}
	if corpus == "" {
		return s.partitionRoot(ctx, sel, cursor)
	}
	params := gapi.FilesListParams{PageToken: cursor, PageSize: 1000}
	switch {
	case strings.HasPrefix(corpus, "user:"):
		if s.admin == nil {
			return page, partitionError{code: police.CodeSweepUnsupported}
		}
		subject, err := s.admin.UserEmail(ctx, strings.TrimPrefix(corpus, "user:"))
		if err != nil {
			return page, partitionFailure(err)
		}
		params.Subject = &subject
		params.Corpora = "user"
		params.Q = partitionQuery("'me' in owners and trashed = false", sel)
	case strings.HasPrefix(corpus, "drive:"):
		id := strings.TrimPrefix(corpus, "drive:")
		params.DriveID = &id
		params.Corpora = "drive"
		params.Q = partitionQuery("trashed = false", sel)
	case strings.HasPrefix(corpus, "folder:"):
		id := strings.TrimPrefix(corpus, "folder:")
		params.Corpora = "user"
		params.Q = partitionQuery("'"+partitionEscape(id)+"' in parents and trashed = false", sel)
	default:
		return page, partitionError{code: police.CodeInvalidCorpus}
	}
	files, err := s.drive.FilesList(ctx, params)
	if err != nil {
		return page, partitionFailure(err)
	}
	ids := []string{}
	for _, file := range files.Files {
		if !file.Trashed && matches(file, sel) && included(file, corpus, sel.DriveIDs, sel.FolderIDs) {
			ids = append(ids, file.ID)
		}
	}
	size := s.cfg.ShardSize
	if size <= 0 {
		size = 100
	}
	for start := 0; start < len(ids); start += size {
		end := start + size
		if end > len(ids) {
			end = len(ids)
		}
		page.Shards = append(page.Shards, partitionShard(corpus, ids[start:end]))
	}
	page.NextCursor = files.NextPageToken
	return page, nil
}
func (s *Service) partitionRoot(ctx context.Context, sel selector, cursor *string) (police.PartitionResult, error) {
	page := police.PartitionResult{Shards: []police.PartitionShard{}, SubCorpora: []police.SubCorpus{}}
	if s.admin == nil {
		for _, id := range sel.DriveIDs {
			page.SubCorpora = append(page.SubCorpora, police.SubCorpus{CorpusKey: "drive:" + id})
		}
		for _, id := range sel.FolderIDs {
			page.SubCorpora = append(page.SubCorpora, police.SubCorpus{CorpusKey: "folder:" + id})
		}
		return page, nil
	}
	current := ""
	if cursor != nil {
		current = *cursor
	}
	if current == "" || strings.HasPrefix(current, "users:") {
		var token *string
		if current != "" {
			t := strings.TrimPrefix(current, "users:")
			if t != "" {
				token = &t
			}
		}
		users, err := s.admin.UsersList(ctx, token)
		if err != nil {
			return page, partitionFailure(err)
		}
		for _, u := range users.Users {
			page.SubCorpora = append(page.SubCorpora, police.SubCorpus{CorpusKey: "user:" + u.ID})
		}
		next := "drives:"
		if users.NextPageToken != nil {
			next = "users:" + *users.NextPageToken
		}
		page.NextCursor = &next
		return page, nil
	}
	if !strings.HasPrefix(current, "drives:") {
		return page, partitionError{code: police.CodeInvalidCorpus}
	}
	var token *string
	if t := strings.TrimPrefix(current, "drives:"); t != "" {
		token = &t
	}
	drives, err := s.drive.DrivesList(ctx, token, s.admin.AdminSubject())
	if err != nil {
		return page, partitionFailure(err)
	}
	for _, d := range drives.Drives {
		page.SubCorpora = append(page.SubCorpora, police.SubCorpus{CorpusKey: "drive:" + d.ID})
	}
	if drives.NextPageToken != nil {
		next := "drives:" + *drives.NextPageToken
		page.NextCursor = &next
	}
	return page, nil
}
func partitionEscape(v string) string {
	return strings.ReplaceAll(strings.ReplaceAll(v, `\`, `\\`), `'`, `\'`)
}
func partitionQuery(base string, sel selector) string {
	mime := ""
	if sel.Kind == "gdocs" {
		mime = "application/vnd.google-apps.document"
	}
	if sel.Kind == "gsheets" {
		mime = "application/vnd.google-apps.spreadsheet"
	}
	if mime != "" {
		return base + " and mimeType = '" + mime + "'"
	}
	if len(sel.MimeTypes) == 0 {
		return base
	}
	clauses := []string{}
	for _, m := range sel.MimeTypes {
		clauses = append(clauses, "mimeType = '"+partitionEscape(m)+"'")
	}
	return base + " and (" + strings.Join(clauses, " or ") + ")"
}
func matches(file gapi.DriveFile, sel selector) bool {
	return matchFile(file, sel.Kind, sel.DriveIDs, sel.FolderIDs, sel.MimeTypes)
}
func partitionShard(corpus string, ids []string) police.PartitionShard {
	sorted := append([]string{}, ids...)
	sort.Strings(sorted)
	digest := sha256.Sum256([]byte(corpus + "\x00" + strings.Join(sorted, ",")))
	return police.PartitionShard{ShardKey: hex.EncodeToString(digest[:]), Descriptor: police.ShardDescriptor{Corpus: corpus, FileIDs: sorted}, Weight: len(sorted)}
}
