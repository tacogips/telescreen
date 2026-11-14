package gdrive

import (
	"encoding/json"
	"fmt"
	"github.com/tacogips/telescreen/go/internal/police"
)

func (s *Service) assemble(task police.TaskEnvelope, outcomes []fetchOutcome, failures int, shard bool, summary string) police.ResultEnvelope {
	dropped := 0
	for {
		r := completed(task)
		r.Summary = summary
		if dropped > 0 {
			if r.Summary != "" {
				r.Summary += "; "
			}
			r.Summary += fmt.Sprintf("size guard removed %d resources", dropped)
		}
		stats := &police.ShardStats{ResourcesFailed: failures}
		for _, o := range outcomes {
			stats.ResourcesScanned++
			stats.BytesScanned += o.bytes
			r.CompleteLocators = append(r.CompleteLocators, o.locator)
			r.Findings = append(r.Findings, o.findings...)
			if o.exposure != nil {
				r.ResourceExposures = append(r.ResourceExposures, *o.exposure)
			}
			if o.inventory != nil {
				r.ResourceInventories = append(r.ResourceInventories, *o.inventory)
			}
		}
		if shard {
			r.Stats = stats
		}
		data, err := police.EncodeResult(r)
		if err != nil {
			return failed(task, police.CodeRawValueGuard, nil)
		}
		if len(data) <= 1048576 {
			var v interface{}
			if json.Unmarshal(data, &v) != nil || s.hasHits(v) {
				return failed(task, police.CodeRawValueGuard, nil)
			}
			return r
		}
		if len(outcomes) == 0 {
			return failed(task, police.CodeRawValueGuard, nil)
		}
		heaviest := 0
		for i := range outcomes {
			if len(outcomes[i].findings) >= len(outcomes[heaviest].findings) {
				heaviest = i
			}
		}
		outcomes = append(outcomes[:heaviest], outcomes[heaviest+1:]...)
		failures++
		dropped++
	}
}
func (s *Service) hasHits(v interface{}) bool {
	switch x := v.(type) {
	case string:
		return s.engine.HasHits(x)
	case []interface{}:
		for _, item := range x {
			if s.hasHits(item) {
				return true
			}
		}
	case map[string]interface{}:
		for k, item := range x {
			if s.engine.HasHits(k) || s.hasHits(item) {
				return true
			}
		}
	}
	return false
}
