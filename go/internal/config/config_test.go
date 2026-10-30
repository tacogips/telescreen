package config

import (
	"errors"
	"strings"
	"testing"
)

func TestLoad(t *testing.T) {
	base := map[string]string{"TELESCREEN_URL": "https://bureau.example.com", "TELESCREEN_POLICE_ENROLLMENT_TOKEN": "private-token", "TELESCREEN_POLICE_FINGERPRINT_KEY": strings.Repeat("k", 32), "TELESCREEN_GOOGLE_SERVICE_ACCOUNT_FILE": "/secret/account"}
	cases := []struct {
		name    string
		changes map[string]string
		remove  []string
		want    string
	}{
		{name: "defaults"},
		{name: "both secrets", changes: map[string]string{"TELESCREEN_POLICE_ENROLLMENT_TOKEN_FILE": "/secret/token"}, want: "TELESCREEN_POLICE_ENROLLMENT_TOKEN"},
		{name: "missing token", remove: []string{"TELESCREEN_POLICE_ENROLLMENT_TOKEN"}, want: "TELESCREEN_POLICE_ENROLLMENT_TOKEN"},
		{name: "short key", changes: map[string]string{"TELESCREEN_POLICE_FINGERPRINT_KEY": strings.Repeat("k", 31)}, want: "TELESCREEN_POLICE_FINGERPRINT_KEY"},
		{name: "file key", remove: []string{"TELESCREEN_POLICE_FINGERPRINT_KEY"}, changes: map[string]string{"TELESCREEN_POLICE_FINGERPRINT_KEY_FILE": "/secret/key"}},
		{name: "http forbidden", changes: map[string]string{"TELESCREEN_URL": "http://example.com"}, want: "TELESCREEN_URL"},
		{name: "http permitted", changes: map[string]string{"TELESCREEN_URL": "http://example.com", "TELESCREEN_POLICE_ALLOW_HTTP": "true"}},
		{name: "bad allow", changes: map[string]string{"TELESCREEN_POLICE_ALLOW_HTTP": "yes"}, want: "TELESCREEN_POLICE_ALLOW_HTTP"},
		{name: "bad label", changes: map[string]string{"TELESCREEN_POLICE_LABELS": "Bad Label"}, want: "TELESCREEN_POLICE_LABELS"},
		{name: "zero shard", changes: map[string]string{"TELESCREEN_SWEEP_SHARD_SIZE": "0"}, want: "TELESCREEN_SWEEP_SHARD_SIZE"},
		{name: "large shard", changes: map[string]string{"TELESCREEN_SWEEP_SHARD_SIZE": "1001"}, want: "TELESCREEN_SWEEP_SHARD_SIZE"},
		{name: "large concurrency", changes: map[string]string{"TELESCREEN_POLICE_CONCURRENCY": "65"}, want: "TELESCREEN_POLICE_CONCURRENCY"},
		{name: "domains", changes: map[string]string{"TELESCREEN_INTERNAL_DOMAINS": " Example.COM, ,b.org"}},
		{name: "bad url", changes: map[string]string{"TELESCREEN_GOOGLE_API_BASE_URL": "https://"}, want: "TELESCREEN_GOOGLE_API_BASE_URL"},
		{name: "bad size", changes: map[string]string{"TELESCREEN_SCAN_MAX_BYTES": "0"}, want: "TELESCREEN_SCAN_MAX_BYTES"},
		{name: "bad name", changes: map[string]string{"TELESCREEN_POLICE_NAME": ""}, want: "TELESCREEN_POLICE_NAME"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			env := map[string]string{}
			for k, v := range base {
				env[k] = v
			}
			for _, k := range tc.remove {
				delete(env, k)
			}
			for k, v := range tc.changes {
				env[k] = v
			}
			c, err := Load(func(k string) (string, bool) { v, ok := env[k]; return v, ok }, func(path string) ([]byte, error) {
				if path == "/secret/key" {
					return []byte(strings.Repeat("x", 32) + "\n"), nil
				}
				return nil, errors.New("sensitive read error")
			})
			if tc.want != "" {
				var ce Error
				if !errors.As(err, &ce) || ce.Variable != tc.want || strings.Contains(err.Error(), "private-token") || strings.Contains(err.Error(), "sensitive read error") {
					t.Fatalf("unexpected error: %v", err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if c.ScanMaxBytes != 5242880 || c.SweepShardSize != 100 || c.Concurrency != 4 || len(c.Labels) != 1 || c.Labels[0] != "police=gdrive" || c.GoogleAPIBaseURL != "https://www.googleapis.com" || c.SheetsBaseURL != "https://sheets.googleapis.com" || c.AdminBaseURL != "https://admin.googleapis.com" || len(c.FingerprintKey) != 32 {
				t.Fatalf("bad defaults: %+v", c)
			}
			if tc.name == "domains" && (len(c.InternalDomains) != 2 || c.InternalDomains[0] != "example.com" || c.InternalDomains[1] != "b.org") {
				t.Fatal(c.InternalDomains)
			}
		})
	}
}
