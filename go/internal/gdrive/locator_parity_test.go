package gdrive

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"

	"github.com/tacogips/telescreen/go/internal/gapi"
	"github.com/tacogips/telescreen/go/internal/police"
)

type locatorAdmin struct{ partAdmin }

func (locatorAdmin) UserEmail(context.Context, string) (string, error) {
	return "user@example.invalid", nil
}

type locatorDrive struct{ partDrive }

func (*locatorDrive) FilesGetMeta(context.Context, string, *string) (gapi.DriveFile, error) {
	return gapi.DriveFile{}, gapi.GoneError{}
}

func TestLocatorParity(t *testing.T) {
	var fixture struct {
		Obfuscation string `json:"obfuscation"`
		Queries     []struct {
			Corpus   string   `json:"corpus_key"`
			Cursor   *string  `json:"cursor"`
			Kind     string   `json:"kind"`
			Selector selector `json:"selector"`
			Expected struct {
				Q         string  `json:"q"`
				Corpora   string  `json:"corpora"`
				DriveID   *string `json:"drive_id"`
				PageToken *string `json:"page_token"`
				PageSize  int     `json:"page_size"`
				Subject   *string `json:"subject"`
			} `json:"expected"`
		} `json:"queries"`
		Refs []struct {
			FileID  string `json:"file_id"`
			Kind    string `json:"kind"`
			Locator string `json:"locator"`
		} `json:"refs"`
		Shards []struct {
			Corpus     string                 `json:"corpus_key"`
			FileIDs    []string               `json:"file_ids"`
			ShardKey   string                 `json:"shard_key"`
			Descriptor police.ShardDescriptor `json:"descriptor"`
			Weight     int                    `json:"weight"`
		} `json:"shards"`
	}
	fixtureRead(t, "locators", &fixture)
	if fixture.Obfuscation != "xor-v1:telescreen-go-parity-fixture" || len(fixture.Queries) == 0 || len(fixture.Refs) == 0 || len(fixture.Shards) == 0 {
		t.Fatal("invalid or empty fixture")
	}
	d := &locatorDrive{}
	s := Service{drive: d, admin: locatorAdmin{}, engine: partEngine{}}
	for i, q := range fixture.Queries {
		q.Selector.Kind = q.Kind
		b, err := json.Marshal(struct {
			Selector selector `json:"selector"`
			Corpus   string   `json:"corpus_key"`
			Cursor   *string  `json:"cursor"`
		}{q.Selector, q.Corpus, q.Cursor})
		if err != nil {
			t.Fatal(err)
		}
		result := s.partition(context.Background(), police.TaskEnvelope{TaskID: "task", Input: b})
		if result.Error != nil {
			t.Fatalf("query %d: %+v", i, result.Error)
		}
		want := gapi.FilesListParams{Q: q.Expected.Q, Corpora: q.Expected.Corpora, DriveID: q.Expected.DriveID, PageToken: q.Expected.PageToken, PageSize: q.Expected.PageSize, Subject: q.Expected.Subject}
		if !reflect.DeepEqual(d.params, want) {
			t.Errorf("query %d: %+v != %+v", i, d.params, want)
		}
	}
	for i, c := range fixture.Refs {
		got := s.fetch(context.Background(), selector{Kind: c.Kind}, c.FileID, nil)
		if got.locator != c.Locator || got.state != fetchRemoved {
			t.Errorf("ref %d: %+v", i, got)
		}
	}
	for i, c := range fixture.Shards {
		got := partitionShard(c.Corpus, c.FileIDs)
		if got.ShardKey != c.ShardKey || got.Weight != c.Weight || !reflect.DeepEqual(got.Descriptor, c.Descriptor) {
			t.Errorf("shard %d: %+v", i, got)
		}
	}
}
