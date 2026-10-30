package gapi

import (
	"context"
	"encoding/json"
	"io"
	"math"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/tacogips/telescreen/go/internal/googleauth"
)

type client struct {
	httpClient     *http.Client
	tokens         *googleauth.TokenSource
	baseURL, scope string
}
type cancelBody struct {
	io.ReadCloser
	cancel context.CancelFunc
}

func (b *cancelBody) Close() error { err := b.ReadCloser.Close(); b.cancel(); return err }
func (c *client) get(ctx context.Context, path string, query url.Values, subject *string) (*http.Response, error) {
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	token, err := c.tokens.Token(ctx, []string{c.scope}, subject)
	if err != nil {
		cancel()
		return nil, FetchFailedError{}
	}
	endpoint := strings.TrimRight(c.baseURL, "/") + path
	if len(query) > 0 {
		endpoint += "?" + query.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		cancel()
		return nil, FetchFailedError{}
	}
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := c.httpClient.Do(req)
	if err != nil {
		cancel()
		return nil, FetchFailedError{}
	}
	resp.Body = &cancelBody{ReadCloser: resp.Body, cancel: cancel}
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return resp, nil
	}
	defer resp.Body.Close()
	switch resp.StatusCode {
	case http.StatusNotFound:
		return nil, GoneError{}
	case http.StatusTooManyRequests:
		return nil, rateLimit(resp)
	case http.StatusForbidden:
		var body struct {
			Error struct {
				Errors []struct {
					Reason string `json:"reason"`
				} `json:"errors"`
			} `json:"error"`
		}
		if json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&body) == nil {
			for _, item := range body.Error.Errors {
				if item.Reason == "rateLimitExceeded" || item.Reason == "userRateLimitExceeded" {
					return nil, rateLimit(resp)
				}
			}
		}
		return nil, ForbiddenError{}
	default:
		return nil, FetchFailedError{}
	}
}
func rateLimit(resp *http.Response) RateLimitedError {
	v, err := strconv.ParseFloat(resp.Header.Get("Retry-After"), 64)
	if err != nil || math.IsNaN(v) || math.IsInf(v, 0) || v < 0 {
		return RateLimitedError{}
	}
	return RateLimitedError{RetryAfterSeconds: &v}
}
func decodeJSON(resp *http.Response, target interface{}) error {
	if json.NewDecoder(resp.Body).Decode(target) != nil {
		return FetchFailedError{}
	}
	return nil
}
