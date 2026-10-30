package police

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestEncoders(t *testing.T) {
	b, e := EncodeResult(ResultEnvelope{Payload: &PartitionResult{}})
	if e != nil || !strings.Contains(string(b), `"payload":{"shards":[],"next_cursor":null,"sub_corpora":[]}`) || strings.Contains(string(b), `"findings"`) || strings.Contains(string(b), `"stats"`) || strings.Contains(string(b), `"complete_locators"`) {
		t.Fatalf("%s %v", b, e)
	}
	b, e = EncodeManifest(Manifest{})
	if e != nil || !strings.Contains(string(b), `"labels":[]`) {
		t.Fatal(string(b), e)
	}
	b, _ = json.Marshal(ResourceExposure{})
	if !strings.Contains(string(b), `"external_principal_count":0`) || !strings.Contains(string(b), `"shared_drive":false`) {
		t.Fatal(string(b))
	}
	b, _ = json.Marshal(ResultError{})
	if strings.Contains(string(b), "retry_after_seconds") {
		t.Fatal(string(b))
	}
	v := 2.5
	b, _ = json.Marshal(ResultError{RetryAfterSeconds: &v})
	if !strings.Contains(string(b), `"retry_after_seconds":2.5`) {
		t.Fatal(string(b))
	}
	b, _ = EncodeResult(ResultEnvelope{Payload: &PartitionResult{Shards: []PartitionShard{{}}}})
	if !strings.Contains(string(b), `"file_ids":[]`) {
		t.Fatal(string(b))
	}
}
func TestTaskExample(t *testing.T) {
	b, e := os.ReadFile("../../../src/telescreen/contracts/police/v1/examples/task-envelope.json")
	if e != nil {
		t.Fatal(e)
	}
	var task TaskEnvelope
	if e = json.Unmarshal(b, &task); e != nil || len(task.Input) == 0 {
		t.Fatal(e)
	}
}
