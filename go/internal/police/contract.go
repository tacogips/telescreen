package police

import (
	"context"
	"encoding/json"
	"time"
)

const (
	ContractVersion       = "1.2"
	TaskDetect            = "detect"
	TaskPartition         = "partition"
	TaskScanShard         = "scan_shard"
	CodeRateLimited       = "rate_limited"
	CodeHandlerError      = "handler_error"
	CodeUnsupportedTask   = "unsupported_task"
	CodeInvalidSelector   = "invalid_selector"
	CodeInvalidDescriptor = "invalid_descriptor"
	CodeInvalidCorpus     = "invalid_corpus"
	CodeSweepUnsupported  = "sweep_unsupported"
	CodePartitionFailed   = "partition_failed"
	CodeRawValueGuard     = "raw_value_guard"
	StatusCompleted       = "completed"
	StatusFailed          = "failed"
)

type Manifest struct {
	Name            string   `json:"name"`
	Version         string   `json:"version"`
	ContractVersion string   `json:"contract_version"`
	Mode            string   `json:"mode"`
	TaskTypes       []string `json:"task_types"`
	Labels          []string `json:"labels"`
	Concurrency     int      `json:"concurrency"`
}
type Registration struct {
	WorkerID                 string `json:"worker_id"`
	WorkerToken              string `json:"worker_token"`
	LeaseSeconds             int    `json:"lease_seconds"`
	HeartbeatIntervalSeconds int    `json:"heartbeat_interval_seconds"`
	MaxWaitSeconds           int    `json:"max_wait_seconds"`
}
type ClaimRequest struct {
	MaxTasks    int `json:"max_tasks"`
	WaitSeconds int `json:"wait_seconds"`
}
type HeartbeatRequest struct {
	TaskIDs []string `json:"task_ids"`
}
type TaskEnvelope struct {
	ContractVersion string          `json:"contract_version"`
	TaskID          string          `json:"task_id"`
	TaskType        string          `json:"task_type"`
	Attempt         int             `json:"attempt"`
	IssuedAt        time.Time       `json:"issued_at"`
	Deadline        time.Time       `json:"deadline"`
	Labels          []string        `json:"labels"`
	Input           json.RawMessage `json:"input"`
}
type MaskedFinding struct {
	Locator             string  `json:"locator"`
	RuleID              string  `json:"rule_id"`
	RuleClass           string  `json:"rule_class"`
	MaskedSnippet       string  `json:"masked_snippet"`
	Location            string  `json:"location"`
	Confidence          float64 `json:"confidence"`
	ExternalFingerprint string  `json:"external_fingerprint"`
}
type ResourceExposure struct {
	Locator                string `json:"locator"`
	Visibility             string `json:"visibility"`
	ExternalPrincipalCount int    `json:"external_principal_count"`
	SharedDrive            bool   `json:"shared_drive"`
}
type ResourceInventory struct {
	Locator   string `json:"locator"`
	Kind      string `json:"kind,omitempty"`
	ItemCount *int   `json:"item_count,omitempty"`
}
type ShardStats struct {
	ResourcesScanned int `json:"resources_scanned"`
	ResourcesFailed  int `json:"resources_failed"`
	BytesScanned     int `json:"bytes_scanned"`
}
type ResultError struct {
	Code              string   `json:"code"`
	Detail            string   `json:"detail"`
	RetryAfterSeconds *float64 `json:"retry_after_seconds,omitempty"`
}
type ShardDescriptor struct {
	Corpus  string   `json:"corpus"`
	FileIDs []string `json:"file_ids"`
}
type PartitionShard struct {
	ShardKey   string          `json:"shard_key"`
	Descriptor ShardDescriptor `json:"descriptor"`
	Weight     int             `json:"weight"`
}
type SubCorpus struct {
	CorpusKey string `json:"corpus_key"`
}
type PartitionResult struct {
	Shards     []PartitionShard `json:"shards"`
	NextCursor *string          `json:"next_cursor"`
	SubCorpora []SubCorpus      `json:"sub_corpora"`
}
type ResultEnvelope struct {
	ContractVersion     string              `json:"contract_version"`
	TaskID              string              `json:"task_id"`
	Status              string              `json:"status"`
	Summary             string              `json:"summary,omitempty"`
	Findings            []MaskedFinding     `json:"findings,omitempty"`
	ResourceExposures   []ResourceExposure  `json:"resource_exposures,omitempty"`
	ResourceInventories []ResourceInventory `json:"resource_inventories,omitempty"`
	Payload             *PartitionResult    `json:"payload,omitempty"`
	Error               *ResultError        `json:"error,omitempty"`
	Stats               *ShardStats         `json:"stats,omitempty"`
	CompleteLocators    []string            `json:"complete_locators,omitempty"`
}
type Handler func(ctx context.Context, task TaskEnvelope) ResultEnvelope

func EncodeResult(r ResultEnvelope) ([]byte, error) {
	if r.Payload != nil {
		p := *r.Payload
		p.Shards = append([]PartitionShard{}, p.Shards...)
		if p.SubCorpora == nil {
			p.SubCorpora = []SubCorpus{}
		}
		for i := range p.Shards {
			if p.Shards[i].Descriptor.FileIDs == nil {
				p.Shards[i].Descriptor.FileIDs = []string{}
			}
		}
		r.Payload = &p
	}
	return json.Marshal(r)
}
func EncodeManifest(m Manifest) ([]byte, error) {
	if m.Labels == nil {
		m.Labels = []string{}
	}
	return json.Marshal(m)
}
func EncodeClaim(c ClaimRequest) ([]byte, error)         { return json.Marshal(c) }
func EncodeHeartbeat(h HeartbeatRequest) ([]byte, error) { return json.Marshal(h) }
