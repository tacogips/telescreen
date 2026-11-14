package police

import (
	"context"
	"time"
)

type backoff struct{ next time.Duration }

func (b *backoff) reset() { b.next = 0 }
func (b *backoff) delay() time.Duration {
	if b.next == 0 {
		b.next = time.Second
	} else if b.next < 30*time.Second {
		b.next *= 2
		if b.next > 30*time.Second {
			b.next = 30 * time.Second
		}
	}
	return b.next
}
func sleepContext(ctx context.Context, d time.Duration) bool {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}
