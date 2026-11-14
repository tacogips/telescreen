package police

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"sync"
	"time"
)

type activeTask struct {
	cancel    context.CancelFunc
	cancelled bool
}
type Worker struct {
	client      *Client
	reg         Registration
	handlers    map[string]Handler
	concurrency int
	logger      *slog.Logger
	now         func() time.Time
	sleep       func(context.Context, time.Duration) bool
	mu          sync.Mutex
	active      map[string]*activeTask
	changed     chan struct{}
}

func NewWorker(c *Client, reg Registration, handlers map[string]Handler, concurrency int, logger *slog.Logger, now func() time.Time) *Worker {
	if concurrency < 1 {
		concurrency = 1
	}
	if logger == nil {
		logger = slog.New(slog.NewTextHandler(io.Discard, nil))
	}
	if now == nil {
		now = time.Now
	}
	return &Worker{client: c, reg: reg, handlers: handlers, concurrency: concurrency, logger: logger, now: now, sleep: sleepContext, active: make(map[string]*activeTask), changed: make(chan struct{}, 1)}
}
func (w *Worker) signal() {
	select {
	case w.changed <- struct{}{}:
	default:
	}
}
func (w *Worker) count() int { w.mu.Lock(); defer w.mu.Unlock(); return len(w.active) }
func transient(err error) bool {
	var b *BureauError
	if errors.As(err, &b) {
		return b.Status == 429 || b.Status >= 500
	}
	var t TransportError
	return errors.As(err, &t)
}
func unauthorized(err error) bool {
	var b *BureauError
	return errors.As(err, &b) && (b.Status == 401 || b.Status == 403)
}
func (w *Worker) Run(ctx context.Context) error {
	heartbeatCtx, stopHeartbeat := context.WithCancel(context.WithoutCancel(ctx))
	claimCtx, stopClaim := context.WithCancel(ctx)
	defer stopClaim()
	auth := make(chan error, 1)
	heartbeatDone := make(chan struct{})
	go func() { defer close(heartbeatDone); w.heartbeat(heartbeatCtx, auth, stopClaim) }()
	var claimBackoff backoff
	var runErr error
	for claimCtx.Err() == nil {
		free := w.concurrency - w.count()
		if free <= 0 {
			select {
			case <-claimCtx.Done():
			case <-w.changed:
			}
			continue
		}
		tasks, err := w.client.Claim(claimCtx, w.reg.WorkerID, w.reg.WorkerToken, ClaimRequest{MaxTasks: free, WaitSeconds: w.reg.MaxWaitSeconds})
		if err != nil {
			if claimCtx.Err() != nil {
				break
			}
			if unauthorized(err) {
				runErr = err
				break
			}
			if transient(err) {
				if !w.sleep(claimCtx, claimBackoff.delay()) {
					break
				}
				continue
			}
			// Malformed responses and other permanent errors cannot safely be retried indefinitely.
			runErr = err
			break
		}
		claimBackoff.reset()
		for _, task := range tasks {
			if claimCtx.Err() != nil || w.count() >= w.concurrency {
				break
			}
			taskCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), task.Deadline.Sub(task.IssuedAt))
			state := &activeTask{cancel: cancel}
			w.mu.Lock()
			if _, exists := w.active[task.TaskID]; exists {
				w.mu.Unlock()
				cancel()
				continue
			}
			w.active[task.TaskID] = state
			w.mu.Unlock()
			go w.execute(taskCtx, task, state)
		}
	}
	for w.count() > 0 {
		<-w.changed
	}
	stopHeartbeat()
	<-heartbeatDone
	select {
	case err := <-auth:
		if runErr == nil {
			runErr = err
		}
	default:
	}
	return runErr
}
func (w *Worker) execute(ctx context.Context, task TaskEnvelope, state *activeTask) {
	started := w.now()
	defer func() { state.cancel(); w.mu.Lock(); delete(w.active, task.TaskID); w.mu.Unlock(); w.signal() }()
	result := w.handle(ctx, task)
	w.mu.Lock()
	cancelled := state.cancelled
	w.mu.Unlock()
	if cancelled {
		return
	}
	var retry backoff
	for ctx.Err() == nil {
		w.mu.Lock()
		cancelled = state.cancelled
		w.mu.Unlock()
		if cancelled {
			return
		}
		err := w.client.SubmitResult(ctx, w.reg.WorkerToken, result)
		if err == nil {
			break
		}
		if !transient(err) {
			var b *BureauError
			if errors.As(err, &b) {
				code := "request_failed"
				switch b.Code {
				case "conflict", "invalid_result", "raw_value_detected":
					code = b.Code
				}
				w.logger.Warn("result rejected", "status", b.Status, "code", code)
			}
			break
		}
		if !w.sleep(ctx, retry.delay()) {
			break
		}
	}
	w.logger.Debug("task finished", "duration_ms", w.now().Sub(started).Milliseconds())
}
func (w *Worker) handle(ctx context.Context, task TaskEnvelope) (result ResultEnvelope) {
	failed := func(code string) ResultEnvelope {
		return ResultEnvelope{ContractVersion: ContractVersion, TaskID: task.TaskID, Status: StatusFailed, Error: &ResultError{Code: code, Detail: "Task handler failed"}}
	}
	defer func() {
		if recover() != nil {
			result = failed(CodeHandlerError)
		}
	}()
	handler, ok := w.handlers[task.TaskType]
	if !ok || handler == nil {
		return failed(CodeUnsupportedTask)
	}
	return handler(ctx, task)
}
func (w *Worker) heartbeat(ctx context.Context, auth chan<- error, stopClaim context.CancelFunc) {
	interval := time.Duration(w.reg.HeartbeatIntervalSeconds) * time.Second
	if interval <= 0 {
		interval = time.Second
	}
	var retry backoff
	for w.sleep(ctx, interval) {
		w.mu.Lock()
		ids := make([]string, 0, len(w.active))
		for id := range w.active {
			ids = append(ids, id)
		}
		w.mu.Unlock()
		if len(ids) == 0 {
			continue
		}
		for {
			cancelled, err := w.client.Heartbeat(ctx, w.reg.WorkerID, w.reg.WorkerToken, ids)
			if err == nil {
				retry.reset()
				w.mu.Lock()
				for _, id := range cancelled {
					if task, ok := w.active[id]; ok {
						task.cancelled = true
						task.cancel()
					}
				}
				w.mu.Unlock()
				break
			}
			if ctx.Err() != nil {
				return
			}
			if unauthorized(err) {
				select {
				case auth <- err:
				default:
				}
				stopClaim()
				return
			}
			if !transient(err) {
				break
			}
			if !w.sleep(ctx, retry.delay()) {
				return
			}
		}
	}
}
