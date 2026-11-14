package police

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestClientRequests(t *testing.T) {
	var paths []string
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.EscapedPath())
		if r.Method != "POST" || r.Header.Get("Content-Type") != "application/json" || r.Header.Get("Authorization") != "Bearer sample" {
			t.Error("incorrect headers or method")
		}
		var body map[string]json.RawMessage
		if json.NewDecoder(r.Body).Decode(&body) != nil {
			t.Error("invalid request")
		}
		switch {
		case strings.HasSuffix(r.URL.Path, "/register"):
			if _, ok := body["name"]; !ok {
				t.Error("manifest missing")
			}
			_, _ = w.Write([]byte(`{"worker_id":"worker","max_wait_seconds":30}`))
		case strings.HasSuffix(r.URL.Path, "/claim"):
			_, _ = w.Write([]byte(`{"tasks":[]}`))
		case strings.HasSuffix(r.URL.Path, "/heartbeat"):
			_, _ = w.Write([]byte(`{"cancelled_task_ids":["one"]}`))
		default:
			_, _ = w.Write([]byte(`{}`))
		}
	}))
	defer s.Close()
	c := NewClient(s.URL+"/", s.Client())
	reg, err := c.Register(context.Background(), Manifest{Name: "sample"}, "sample")
	if err != nil || reg.WorkerID != "worker" || reg.MaxWaitSeconds != 30 {
		t.Fatalf("register: %+v %v", reg, err)
	}
	tasks, err := c.Claim(context.Background(), "a/b", "sample", ClaimRequest{MaxTasks: 1})
	if err != nil || len(tasks) != 0 {
		t.Fatalf("claim: %v", err)
	}
	cancelled, err := c.Heartbeat(context.Background(), "a/b", "sample", []string{"one"})
	if err != nil || len(cancelled) != 1 || cancelled[0] != "one" {
		t.Fatalf("heartbeat: %v", err)
	}
	if err := c.SubmitResult(context.Background(), "sample", ResultEnvelope{TaskID: "a/b"}); err != nil {
		t.Fatal(err)
	}
	for _, i := range []int{1, 2, 3} {
		if !strings.Contains(paths[i], "a%2Fb") {
			t.Errorf("path not escaped: %s", paths[i])
		}
	}
}

func TestClientInvalidAndErrorResponses(t *testing.T) {
	response := ""
	status := 200
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(status); _, _ = w.Write([]byte(response)) }))
	defer s.Close()
	c := NewClient(s.URL, s.Client())
	for _, response = range []string{`{}`, `{"tasks":null}`, `{"tasks":{}}`, `{"tasks":"x"}`} {
		if _, err := c.Claim(context.Background(), "w", "t", ClaimRequest{}); err == nil {
			t.Errorf("claim accepted %s", response)
		}
	}
	for _, response = range []string{`{}`, `{"cancelled_task_ids":null}`, `{"cancelled_task_ids":[null]}`, `{"cancelled_task_ids":[1]}`} {
		if _, err := c.Heartbeat(context.Background(), "w", "t", nil); err == nil {
			t.Errorf("heartbeat accepted %s", response)
		}
	}
	status, response = 409, `{"error":{"code":"conflict"}}`
	_, err := c.Claim(context.Background(), "w", "t", ClaimRequest{})
	var bureau *BureauError
	if !errors.As(err, &bureau) || bureau.Status != 409 || bureau.Code != "conflict" {
		t.Fatalf("unexpected error %v", err)
	}
	status, response = 500, `not json`
	_, err = c.Claim(context.Background(), "w", "t", ClaimRequest{})
	if !errors.As(err, &bureau) || bureau.Code != "request_failed" {
		t.Fatalf("unexpected error %v", err)
	}
	s.Close()
	_, err = c.Claim(context.Background(), "w", "t", ClaimRequest{})
	var transport TransportError
	if !errors.As(err, &transport) || strings.Contains(err.Error(), s.URL) {
		t.Fatalf("unsafe transport error %v", err)
	}
}
