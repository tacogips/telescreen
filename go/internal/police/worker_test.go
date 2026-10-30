package police

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func workerTask(id, kind string) TaskEnvelope {
	issued := time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC)
	return TaskEnvelope{ContractVersion: ContractVersion, TaskID: id, TaskType: kind, IssuedAt: issued, Deadline: issued.Add(2 * time.Minute), Input: json.RawMessage(`{}`)}
}

func workerReg() Registration {
	return Registration{WorkerID: "worker", WorkerToken: "worker-test-value", HeartbeatIntervalSeconds: 1, MaxWaitSeconds: 1}
}
func workerResult(task TaskEnvelope) ResultEnvelope {
	return ResultEnvelope{ContractVersion: ContractVersion, TaskID: task.TaskID, Status: StatusCompleted}
}
func writeTasks(w http.ResponseWriter, tasks ...TaskEnvelope) {
	if tasks == nil {
		tasks = []TaskEnvelope{}
	}
	_ = json.NewEncoder(w).Encode(struct {
		Tasks []TaskEnvelope `json:"tasks"`
	}{tasks})
}
func await[T any](t *testing.T, ch <-chan T) T {
	t.Helper()
	select {
	case v := <-ch:
		return v
	case <-time.After(8 * time.Second):
		t.Fatal("timed out waiting for worker")
		var zero T
		return zero
	}
}

func TestWorkerPanicUnknownAndSubmitRejection(t *testing.T) {
	for _, tc := range []struct {
		name, taskType, code string
		handler              Handler
	}{
		{"panic", "panic", CodeHandlerError, func(context.Context, TaskEnvelope) ResultEnvelope { panic("private details") }},
		{"unknown", "unknown", CodeUnsupportedTask, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var claims atomic.Int32
			var submits atomic.Int32
			result := make(chan ResultEnvelope, 2)
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch {
				case strings.HasSuffix(r.URL.Path, "/claim"):
					if claims.Add(1) == 1 {
						writeTasks(w, workerTask("task", tc.taskType))
					} else {
						time.Sleep(10 * time.Millisecond)
						writeTasks(w)
					}
				case strings.HasSuffix(r.URL.Path, "/result"):
					submits.Add(1)
					var got ResultEnvelope
					_ = json.NewDecoder(r.Body).Decode(&got)
					result <- got
					w.WriteHeader(http.StatusUnprocessableEntity)
					_, _ = w.Write([]byte(`{"error":{"code":"invalid_result"}}`))
				default:
					_, _ = w.Write([]byte(`{"cancelled_task_ids":[]}`))
				}
			}))
			defer srv.Close()
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			handlers := map[string]Handler{}
			if tc.handler != nil {
				handlers[tc.taskType] = tc.handler
			}
			var logs bytes.Buffer
			worker := NewWorker(NewClient(srv.URL, srv.Client()), workerReg(), handlers, 1, slog.New(slog.NewTextHandler(&logs, nil)), time.Now)
			done := make(chan error, 1)
			go func() { done <- worker.Run(ctx) }()
			got := await(t, result)
			if got.Status != StatusFailed || got.Error == nil || got.Error.Code != tc.code || got.Error.Detail != "Task handler failed" {
				t.Fatalf("unexpected failure result: %+v", got)
			}
			deadline := time.After(3 * time.Second)
			for claims.Load() < 2 {
				select {
				case <-deadline:
					t.Fatal("did not continue after rejection")
				default:
					time.Sleep(time.Millisecond)
				}
			}
			cancel()
			if err := await(t, done); err != nil {
				t.Fatal(err)
			}
			if submits.Load() != 1 {
				t.Fatalf("submissions: %d", submits.Load())
			}
			if strings.Contains(logs.String(), workerReg().WorkerToken) || strings.Contains(logs.String(), "private details") {
				t.Fatal("sensitive data logged")
			}
		})
	}
}

func TestWorkerCapacityAndGracefulStopWithClockSkew(t *testing.T) {
	started := make(chan string, 2)
	release := make(chan struct{})
	results := make(chan string, 2)
	var claims atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/claim"):
			claims.Add(1)
			writeTasks(w, workerTask("first", "detect"), workerTask("extra", "detect"))
		case strings.HasSuffix(r.URL.Path, "/result"):
			var got ResultEnvelope
			_ = json.NewDecoder(r.Body).Decode(&got)
			results <- got.TaskID
		default:
			_, _ = w.Write([]byte(`{"cancelled_task_ids":[]}`))
		}
	}))
	defer srv.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	handler := func(ctx context.Context, task TaskEnvelope) ResultEnvelope {
		started <- task.TaskID
		select {
		case <-release:
		case <-ctx.Done():
			t.Error("graceful stop or clock skew cancelled handler")
		}
		return workerResult(task)
	}
	worker := NewWorker(NewClient(srv.URL, srv.Client()), workerReg(), map[string]Handler{"detect": handler}, 1, slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil)), func() time.Time { return time.Now().Add(10 * time.Hour) })
	done := make(chan error, 1)
	go func() { done <- worker.Run(ctx) }()
	if id := await(t, started); id != "first" {
		t.Fatalf("started %s", id)
	}
	cancel()
	select {
	case err := <-done:
		t.Fatalf("returned before task completed: %v", err)
	case <-time.After(50 * time.Millisecond):
	}
	close(release)
	if id := await(t, results); id != "first" {
		t.Fatalf("submitted %s", id)
	}
	if err := await(t, done); err != nil {
		t.Fatal(err)
	}
	if claims.Load() != 1 {
		t.Fatalf("claims after stop: %d", claims.Load())
	}
	select {
	case id := <-started:
		t.Fatalf("extra task ran: %s", id)
	default:
	}
}

func TestWorkerHeartbeatCancellation(t *testing.T) {
	started := make(chan struct{})
	cancelled := make(chan struct{})
	release := make(chan struct{})
	var submits atomic.Int32
	var claims atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/claim"):
			if claims.Add(1) == 1 {
				writeTasks(w, workerTask("cancelled", "detect"))
			} else {
				time.Sleep(10 * time.Millisecond)
				writeTasks(w)
			}
		case strings.HasSuffix(r.URL.Path, "/heartbeat"):
			_, _ = w.Write([]byte(`{"cancelled_task_ids":["cancelled"]}`))
		case strings.HasSuffix(r.URL.Path, "/result"):
			submits.Add(1)
		}
	}))
	defer srv.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	handler := func(taskCtx context.Context, task TaskEnvelope) ResultEnvelope {
		close(started)
		<-taskCtx.Done()
		close(cancelled)
		<-release
		return workerResult(task)
	}
	worker := NewWorker(NewClient(srv.URL, srv.Client()), workerReg(), map[string]Handler{"detect": handler}, 1, slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil)), time.Now)
	done := make(chan error, 1)
	go func() { done <- worker.Run(ctx) }()
	await(t, started)
	await(t, cancelled)
	cancel()
	close(release)
	if err := await(t, done); err != nil {
		t.Fatal(err)
	}
	if submits.Load() != 0 {
		t.Fatalf("cancelled task submitted %d times", submits.Load())
	}
}

func TestWorkerClaimUnauthorized(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"error":{"code":"unauthorized"}}`))
	}))
	defer srv.Close()
	worker := NewWorker(NewClient(srv.URL, srv.Client()), workerReg(), nil, 1, slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil)), time.Now)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	err := worker.Run(ctx)
	var bureau *BureauError
	if !errors.As(err, &bureau) || bureau.Status != 401 {
		t.Fatalf("got %v", err)
	}
}

func TestWorkerClaimBackoffAndEmptyClaim(t *testing.T) {
	var claims atomic.Int32
	var mu sync.Mutex
	var delays []time.Duration
	submitted := make(chan struct{}, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/claim"):
			switch claims.Add(1) {
			case 1, 2:
				w.WriteHeader(500)
				_, _ = w.Write([]byte("failed"))
			case 3:
				writeTasks(w)
			case 4:
				writeTasks(w, workerTask("retry", "detect"))
			default:
				time.Sleep(10 * time.Millisecond)
				writeTasks(w)
			}
		case strings.HasSuffix(r.URL.Path, "/result"):
			submitted <- struct{}{}
		default:
			_, _ = w.Write([]byte(`{"cancelled_task_ids":[]}`))
		}
	}))
	defer srv.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	reg := workerReg()
	reg.HeartbeatIntervalSeconds = 3600
	worker := NewWorker(NewClient(srv.URL, srv.Client()), reg, map[string]Handler{"detect": func(_ context.Context, task TaskEnvelope) ResultEnvelope { return workerResult(task) }}, 1, slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil)), time.Now)
	worker.sleep = func(ctx context.Context, d time.Duration) bool {
		if d == time.Hour {
			<-ctx.Done()
			return false
		}
		mu.Lock()
		delays = append(delays, d)
		mu.Unlock()
		return ctx.Err() == nil
	}
	done := make(chan error, 1)
	go func() { done <- worker.Run(ctx) }()
	await(t, submitted)
	cancel()
	if err := await(t, done); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(delays) < 2 || delays[0] != time.Second || delays[1] != 2*time.Second {
		t.Fatal(fmt.Sprintf("unexpected delays %v", delays))
	}
	if claims.Load() < 4 {
		t.Fatalf("claims %d", claims.Load())
	}
}

func TestWorkerHeartbeatUnauthorizedInterruptsClaim(t *testing.T) {
	var claims atomic.Int32
	heartbeat := make(chan struct{})
	release := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/claim"):
			_, _ = io.Copy(io.Discard, r.Body)
			if claims.Add(1) == 1 {
				writeTasks(w, workerTask("active", "detect"))
			} else {
				<-r.Context().Done()
			}
		case strings.HasSuffix(r.URL.Path, "/heartbeat"):
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = w.Write([]byte(`{"error":{"code":"unauthorized"}}`))
			close(heartbeat)
		}
	}))
	defer server.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	handler := func(_ context.Context, task TaskEnvelope) ResultEnvelope { <-release; return workerResult(task) }
	worker := NewWorker(NewClient(server.URL, server.Client()), workerReg(), map[string]Handler{"detect": handler}, 2, slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil)), time.Now)
	done := make(chan error, 1)
	go func() { done <- worker.Run(ctx) }()
	await(t, heartbeat)
	close(release)
	err := await(t, done)
	var bureau *BureauError
	if !errors.As(err, &bureau) || bureau.Status != http.StatusUnauthorized {
		t.Fatalf("got %v", err)
	}
	if claims.Load() < 2 {
		t.Fatal("no outstanding claim")
	}
}
