package envelopes

import (
	"bytes"
	"flag"
	"os"
	"path/filepath"
	"testing"

	"github.com/tacogips/telescreen/go/internal/police"
)

var update bool

func TestMain(m *testing.M) {
	flag.BoolVar(&update, "update", false, "rewrite envelope goldens")
	flag.Parse()
	os.Exit(m.Run())
}

func TestGoldenEnvelopes(t *testing.T) {
	locator1 := "gdrive:fileid0001"
	locator2 := "gdrive:fileid0002"
	finding := police.MaskedFinding{Locator: locator1, RuleID: "example", RuleClass: "credential", MaskedSnippet: "key=********[24]", Location: "[REDACTED]", Confidence: 0.9, ExternalFingerprint: "abcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcd"}
	exposure := police.ResourceExposure{Locator: locator1, Visibility: "private"}
	count := 2
	cursor := "page:next"
	rootCursor := "drives:"
	retry := 7.0
	completed := func(id string) police.ResultEnvelope {
		return police.ResultEnvelope{ContractVersion: police.ContractVersion, TaskID: id, Status: police.StatusCompleted}
	}
	detect := completed("detect")
	detect.Findings = []police.MaskedFinding{finding}
	detect.CompleteLocators = []string{locator1}
	exposures := detect
	exposures.TaskID = "detect-exposures"
	exposures.ResourceExposures = []police.ResourceExposure{exposure}
	scan := completed("scan-shard")
	scan.Findings = []police.MaskedFinding{finding}
	scan.CompleteLocators = []string{locator1, locator2}
	scan.ResourceExposures = []police.ResourceExposure{exposure}
	scan.ResourceInventories = []police.ResourceInventory{{Locator: locator1, Kind: "table", ItemCount: &count}, {Locator: locator2, Kind: "file"}}
	scan.Stats = &police.ShardStats{ResourcesScanned: 2, ResourcesFailed: 1, BytesScanned: 100}
	partition := completed("partition")
	partition.Payload = &police.PartitionResult{Shards: []police.PartitionShard{{ShardKey: "shard-a", Descriptor: police.ShardDescriptor{Corpus: "drive:example", FileIDs: []string{"fileid0001"}}, Weight: 1}, {ShardKey: "shard-b", Descriptor: police.ShardDescriptor{Corpus: "drive:example", FileIDs: []string{"fileid0002"}}, Weight: 1}}, NextCursor: &cursor, SubCorpora: []police.SubCorpus{}}
	root := completed("partition-root")
	root.Payload = &police.PartitionResult{Shards: []police.PartitionShard{}, NextCursor: &rootCursor, SubCorpora: []police.SubCorpus{{CorpusKey: "drive:alpha"}, {CorpusKey: "drive:beta"}}}
	failed := police.ResultEnvelope{ContractVersion: police.ContractVersion, TaskID: "failed", Status: police.StatusFailed, Error: &police.ResultError{Code: police.CodeRateLimited, Detail: "rate limited", RetryAfterSeconds: &retry}}
	cases := []struct {
		name   string
		encode func() ([]byte, error)
	}{
		{"manifest", func() ([]byte, error) {
			return police.EncodeManifest(police.Manifest{Name: "gdrive", Version: "1.0", ContractVersion: police.ContractVersion, Mode: "pull", TaskTypes: []string{police.TaskDetect, police.TaskPartition, police.TaskScanShard}, Labels: []string{"police=gdrive"}, Concurrency: 4})
		}},
		{"claim-request", func() ([]byte, error) { return police.EncodeClaim(police.ClaimRequest{MaxTasks: 4, WaitSeconds: 10}) }},
		{"heartbeat-request", func() ([]byte, error) {
			return police.EncodeHeartbeat(police.HeartbeatRequest{TaskIDs: []string{"detect", "partition"}})
		}},
		{"result-detect-findings", func() ([]byte, error) { return police.EncodeResult(detect) }},
		{"result-detect-exposures", func() ([]byte, error) { return police.EncodeResult(exposures) }},
		{"result-scan-shard", func() ([]byte, error) { return police.EncodeResult(scan) }},
		{"result-partition", func() ([]byte, error) { return police.EncodeResult(partition) }},
		{"result-partition-root", func() ([]byte, error) { return police.EncodeResult(root) }},
		{"result-failed-rate-limited", func() ([]byte, error) { return police.EncodeResult(failed) }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			encoded, err := tc.encode()
			if err != nil {
				t.Fatal(err)
			}
			path := filepath.Join("../../testdata/envelopes", tc.name+".json")
			if update {
				if err := os.WriteFile(path, encoded, 0644); err != nil {
					t.Fatal(err)
				}
			}
			golden, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(encoded, golden) {
				t.Fatalf("encoded envelope differs from %s", path)
			}
		})
	}
}
