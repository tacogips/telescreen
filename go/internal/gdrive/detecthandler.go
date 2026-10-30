package gdrive

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/tacogips/telescreen/go/internal/police"
)

func (s *Service) detect(ctx context.Context, task police.TaskEnvelope) police.ResultEnvelope {
	var in struct {
		Selector json.RawMessage `json:"selector"`
	}
	if json.Unmarshal(task.Input, &in) != nil {
		return failed(task, police.CodeInvalidSelector, nil)
	}
	sel, ok := parseSelector(in.Selector)
	if !ok {
		return failed(task, police.CodeInvalidSelector, nil)
	}
	corpora := make([]string, 0, len(sel.DriveIDs)+len(sel.FolderIDs))
	for _, id := range sel.DriveIDs {
		corpora = append(corpora, "drive:"+id)
	}
	for _, id := range sel.FolderIDs {
		corpora = append(corpora, "folder:"+id)
	}
	outcomes := []fetchOutcome{}
	scanned, omitted := 0, 0
	for _, corpus := range corpora {
		var cursor *string
		for {
			page, err := s.partitionPage(ctx, sel, corpus, cursor)
			if err != nil {
				pe := partitionFailure(err)
				if p, ok := err.(partitionError); ok {
					pe = p
				}
				return failed(task, pe.code, pe.retry)
			}
			for _, shard := range page.Shards {
				for _, id := range shard.Descriptor.FileIDs {
					if scanned >= 1000 {
						omitted++
						continue
					}
					scanned++
					o := s.fetch(ctx, sel, id, nil)
					if o.state == fetchRateLimited {
						return failed(task, police.CodeRateLimited, o.retryAfter)
					}
					if o.state != fetchFailed {
						outcomes = append(outcomes, o)
					}
				}
			}
			if page.NextCursor == nil {
				break
			}
			cursor = page.NextCursor
		}
	}
	summary := ""
	if omitted > 0 {
		summary = fmt.Sprintf("scanned %d; not scanned %d (limit 1000); use a sweep", scanned, omitted)
	}
	return s.assemble(task, outcomes, 0, false, summary)
}
