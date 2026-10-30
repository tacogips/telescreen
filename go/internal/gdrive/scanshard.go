package gdrive

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/tacogips/telescreen/go/internal/gapi"
	"github.com/tacogips/telescreen/go/internal/police"
	"strings"
)

func (s *Service) scanShard(ctx context.Context, task police.TaskEnvelope) police.ResultEnvelope {
	var in struct {
		Selector   json.RawMessage `json:"selector"`
		Descriptor json.RawMessage `json:"descriptor"`
	}
	if json.Unmarshal(task.Input, &in) != nil {
		return failed(task, police.CodeInvalidDescriptor, nil)
	}
	sel, ok := parseSelector(in.Selector)
	if !ok {
		return failed(task, police.CodeInvalidSelector, nil)
	}
	var desc struct {
		Corpus  *string   `json:"corpus"`
		FileIDs *[]string `json:"file_ids"`
	}
	if json.Unmarshal(in.Descriptor, &desc) != nil || desc.Corpus == nil || desc.FileIDs == nil {
		return failed(task, police.CodeInvalidDescriptor, nil)
	}
	var descriptorFields map[string]json.RawMessage
	if json.Unmarshal(in.Descriptor, &descriptorFields) != nil || !stringArray(descriptorFields["file_ids"]) {
		return failed(task, police.CodeInvalidDescriptor, nil)
	}
	var subject *string
	if strings.HasPrefix(*desc.Corpus, "user:") {
		if s.admin == nil {
			return s.assemble(task, nil, len(*desc.FileIDs), true, "")
		}
		email, err := s.admin.UserEmail(ctx, strings.TrimPrefix(*desc.Corpus, "user:"))
		if err != nil {
			var rate gapi.RateLimitedError
			if errors.As(err, &rate) {
				return failed(task, police.CodeRateLimited, rate.RetryAfterSeconds)
			}
			return s.assemble(task, nil, len(*desc.FileIDs), true, "")
		}
		subject = &email
	}
	outcomes := make([]fetchOutcome, 0, len(*desc.FileIDs))
	failures := 0
	for _, id := range *desc.FileIDs {
		o := s.fetch(ctx, sel, id, subject)
		if o.state == fetchRateLimited {
			return failed(task, police.CodeRateLimited, o.retryAfter)
		}
		if o.state == fetchFailed {
			failures++
			continue
		}
		outcomes = append(outcomes, o)
	}
	return s.assemble(task, outcomes, failures, true, "")
}
