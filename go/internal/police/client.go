package police

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
)

type Client struct {
	baseURL string
	http    *http.Client
}

func NewClient(baseURL string, httpClient *http.Client) *Client {
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{baseURL: strings.TrimRight(baseURL, "/") + "/api/police/v1", http: httpClient}
}

type BureauError struct {
	Status int
	Code   string
}

func (e *BureauError) Error() string {
	return fmt.Sprintf("bureau request failed (%d): %s", e.Status, e.Code)
}

type TransportError struct{}

func (TransportError) Error() string { return "bureau transport failed" }

func (c *Client) post(ctx context.Context, path, token string, body []byte) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(body))
	if err != nil {
		return nil, TransportError{}
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, TransportError{}
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, TransportError{}
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		var envelope struct {
			Error struct {
				Code string `json:"code"`
			} `json:"error"`
		}
		code := "request_failed"
		if json.Unmarshal(data, &envelope) == nil && envelope.Error.Code != "" {
			code = envelope.Error.Code
		}
		return nil, &BureauError{Status: resp.StatusCode, Code: code}
	}
	return data, nil
}

func (c *Client) Register(ctx context.Context, m Manifest, enrollmentToken string) (Registration, error) {
	var result Registration
	body, err := EncodeManifest(m)
	if err != nil {
		return result, err
	}
	data, err := c.post(ctx, "/workers/register", enrollmentToken, body)
	if err != nil {
		return result, err
	}
	if json.Unmarshal(data, &result) != nil {
		return Registration{}, errors.New("invalid Bureau response")
	}
	return result, nil
}

func (c *Client) Claim(ctx context.Context, workerID, token string, req ClaimRequest) ([]TaskEnvelope, error) {
	body, err := EncodeClaim(req)
	if err != nil {
		return nil, err
	}
	data, err := c.post(ctx, "/workers/"+url.PathEscape(workerID)+"/claim", token, body)
	if err != nil {
		return nil, err
	}
	var response struct {
		Tasks json.RawMessage `json:"tasks"`
	}
	if json.Unmarshal(data, &response) != nil || len(response.Tasks) == 0 || response.Tasks[0] != '[' {
		return nil, errors.New("invalid Bureau response")
	}
	var tasks []TaskEnvelope
	if json.Unmarshal(response.Tasks, &tasks) != nil {
		return nil, errors.New("invalid Bureau response")
	}
	return tasks, nil
}

func (c *Client) Heartbeat(ctx context.Context, workerID, token string, taskIDs []string) ([]string, error) {
	body, err := EncodeHeartbeat(HeartbeatRequest{TaskIDs: taskIDs})
	if err != nil {
		return nil, err
	}
	data, err := c.post(ctx, "/workers/"+url.PathEscape(workerID)+"/heartbeat", token, body)
	if err != nil {
		return nil, err
	}
	var response struct {
		Cancelled json.RawMessage `json:"cancelled_task_ids"`
	}
	if json.Unmarshal(data, &response) != nil || len(response.Cancelled) == 0 || response.Cancelled[0] != '[' {
		return nil, errors.New("invalid Bureau response")
	}
	var items []json.RawMessage
	if json.Unmarshal(response.Cancelled, &items) != nil {
		return nil, errors.New("invalid Bureau response")
	}
	result := make([]string, 0, len(items))
	for _, item := range items {
		var value string
		if len(item) == 0 || item[0] != '"' || json.Unmarshal(item, &value) != nil {
			return nil, errors.New("invalid Bureau response")
		}
		result = append(result, value)
	}
	return result, nil
}

func (c *Client) SubmitResult(ctx context.Context, token string, r ResultEnvelope) error {
	body, err := EncodeResult(r)
	if err != nil {
		return err
	}
	_, err = c.post(ctx, "/tasks/"+url.PathEscape(r.TaskID)+"/result", token, body)
	return err
}
