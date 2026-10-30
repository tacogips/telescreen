package gapi

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"sync/atomic"
	"testing"
	"time"

	"github.com/tacogips/telescreen/go/internal/googleauth"
)

func fakeTokens(t *testing.T, endpoint string) *googleauth.TokenSource {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	payload, err := json.Marshal(struct {
		Email string `json:"client_email"`
		Key   string `json:"private_key"`
		URI   string `json:"token_uri"`
	}{"worker@example.invalid", string(pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(key)})), endpoint})
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := googleauth.ParseServiceAccountKey(payload)
	if err != nil {
		t.Fatal(err)
	}
	return googleauth.NewTokenSource(http.DefaultClient, parsed, time.Now)
}
func TestClients(t *testing.T) {
	var status atomic.Int32
	var emailCalls atomic.Int32
	status.Store(200)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/token" {
			fmt.Fprint(w, `{"access_token":"test-access","expires_in":3600}`)
			return
		}
		if r.Header.Get("Authorization") != "Bearer test-access" {
			t.Error("missing authorization")
		}
		if s := status.Load(); s != 200 {
			if s == 429 {
				w.Header().Set("Retry-After", "7")
			}
			w.WriteHeader(int(s))
			if s == 403 {
				fmt.Fprint(w, `{"error":{"errors":[{"reason":"userRateLimitExceeded"}]}}`)
			}
			return
		}
		switch r.URL.Path {
		case "/drive/v3/files":
			want := url.Values{"q": {"q"}, "corpora": {"drive"}, "pageSize": {"1000"}, "supportsAllDrives": {"true"}, "includeItemsFromAllDrives": {"true"}, "fields": {"files(id,name,mimeType,trashed,driveId,parents,owners(emailAddress),modifiedTime,size),nextPageToken"}, "driveId": {"d"}, "pageToken": {"p"}}
			if !reflect.DeepEqual(r.URL.Query(), want) {
				t.Errorf("query: %v", r.URL.Query())
			}
			fmt.Fprint(w, `{"files":[{"id":"f"}],"nextPageToken":"next"}`)
		case "/drive/v3/files/f/permissions":
			switch r.URL.Query().Get("pageToken") {
			case "":
				fmt.Fprint(w, `{"permissions":[{"type":"one"}],"nextPageToken":"2"}`)
			case "2":
				fmt.Fprint(w, `{"permissions":[{"type":"two"}],"nextPageToken":"3"}`)
			default:
				fmt.Fprint(w, `{"permissions":[{"type":"three"}]}`)
			}
		case "/drive/v3/files/f":
			fmt.Fprint(w, "12345")
		case "/v4/spreadsheets/s/values:batchGet":
			if !reflect.DeepEqual(r.URL.Query()["ranges"], []string{"first", "second"}) || r.URL.Query().Get("valueRenderOption") != "FORMATTED_VALUE" {
				t.Error("ranges")
			}
			fmt.Fprint(w, `{"valueRanges":[{"range":"first","values":[["x",1,true]]},{"range":"second"}]}`)
		case "/admin/directory/v1/users/u":
			emailCalls.Add(1)
			fmt.Fprint(w, `{"primaryEmail":"user@example.invalid"}`)
		default:
			t.Errorf("unexpected path %s", r.URL.Path)
			w.WriteHeader(500)
		}
	}))
	defer server.Close()
	tokens := fakeTokens(t, server.URL+"/token")
	ctx := context.Background()
	drive := NewDrive(server.Client(), tokens, server.URL)
	d, p := "d", "p"
	page, err := drive.FilesList(ctx, FilesListParams{Q: "q", Corpora: "drive", PageSize: 1200, DriveID: &d, PageToken: &p})
	if err != nil || page.NextPageToken == nil || *page.NextPageToken != "next" {
		t.Fatal(page, err)
	}
	perms, err := drive.ListPermissions(ctx, "f", nil)
	if err != nil || len(perms) != 3 {
		t.Fatal(perms, err)
	}
	content, oversize, err := drive.Download(ctx, "f", nil, 5)
	if err != nil || oversize || string(content) != "12345" {
		t.Fatal(oversize, err)
	}
	content, oversize, err = drive.Download(ctx, "f", nil, 4)
	if err != nil || !oversize || content != nil {
		t.Fatal(oversize, err)
	}
	sheets := NewSheets(server.Client(), tokens, server.URL)
	values, err := sheets.ValuesBatchGet(ctx, "s", []string{"first", "second"}, nil)
	if err != nil || len(values) != 2 || len(values[0].Values[0]) != 3 || len(values[1].Values) != 0 || values[0].Values[0][2].Kind != CellBool {
		t.Fatal(values, err)
	}
	admin := NewAdmin(server.Client(), tokens, server.URL, "admin@example.invalid")
	for i := 0; i < 2; i++ {
		if _, err := admin.UserEmail(ctx, "u"); err != nil {
			t.Fatal(err)
		}
	}
	if emailCalls.Load() != 1 {
		t.Fatal(emailCalls.Load())
	}
	for _, tc := range []struct {
		code  int
		check func(error) bool
	}{{404, func(e error) bool { _, ok := e.(GoneError); return ok }}, {410, func(e error) bool { _, ok := e.(FetchFailedError); return ok }}, {500, func(e error) bool { _, ok := e.(FetchFailedError); return ok }}, {429, func(e error) bool {
		v, ok := e.(RateLimitedError)
		return ok && v.RetryAfterSeconds != nil && *v.RetryAfterSeconds == 7
	}}, {403, func(e error) bool { v, ok := e.(RateLimitedError); return ok && v.RetryAfterSeconds == nil }}} {
		status.Store(int32(tc.code))
		_, err := drive.FilesGetMeta(ctx, "f", nil)
		if !tc.check(err) {
			t.Errorf("status %d: %v", tc.code, err)
		}
	}
}

func TestFailures(t *testing.T) {
	var response atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/token" {
			if response.Load() == 0 {
				w.WriteHeader(500)
				return
			}
			fmt.Fprint(w, `{"access_token":"sample","expires_in":3600}`)
			return
		}
		switch response.Load() {
		case 1:
			w.WriteHeader(403)
			fmt.Fprint(w, `{"error":{"errors":[{"reason":"insufficientPermissions"}]}}`)
		case 2:
			w.WriteHeader(403)
			fmt.Fprint(w, "bad response")
		case 3:
			w.Header().Set("Retry-After", "NaN")
			w.WriteHeader(429)
		}
	}))
	defer server.Close()
	drive := NewDrive(server.Client(), fakeTokens(t, server.URL+"/token"), server.URL)
	_, err := drive.FilesGetMeta(context.Background(), "f", nil)
	if _, ok := err.(FetchFailedError); !ok {
		t.Fatalf("token failure: %T", err)
	}
	for _, mode := range []int32{1, 2, 3} {
		response.Store(mode)
		_, err = drive.FilesGetMeta(context.Background(), "f", nil)
		if mode == 3 {
			v, ok := err.(RateLimitedError)
			if !ok || v.RetryAfterSeconds != nil {
				t.Fatalf("rate limit: %v", err)
			}
		} else if _, ok := err.(ForbiddenError); !ok {
			t.Fatalf("forbidden: %T", err)
		}
	}
}
