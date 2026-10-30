// Package config loads the worker's environment-only configuration.
package config

import (
	"fmt"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

type Config struct {
	BureauURL, EnrollmentToken                    string
	FingerprintKey                                []byte
	ServiceAccountFile                            string
	DelegatedAdmin                                *string
	InternalDomains                               []string
	ScanMaxBytes, SweepShardSize                  int
	Name                                          string
	Labels                                        []string
	Concurrency                                   int
	GoogleAPIBaseURL, SheetsBaseURL, AdminBaseURL string
	AllowHTTP                                     bool
}

type Error struct {
	Variable string
	Reason   string
}

func (e Error) Error() string {
	return fmt.Sprintf("invalid configuration: %s: %s", e.Variable, e.Reason)
}

func Load(lookup func(string) (string, bool), readFile func(string) ([]byte, error)) (Config, error) {
	var c Config
	required := func(name string) (string, error) {
		v, ok := lookup(name)
		if !ok || v == "" {
			return "", Error{name, "required"}
		}
		return v, nil
	}
	secret := func(name string) (string, error) {
		v, direct := lookup(name)
		path, file := lookup(name + "_FILE")
		if direct == file {
			return "", Error{name, "set exactly one of the value or file"}
		}
		if file {
			if path == "" {
				return "", Error{name + "_FILE", "required"}
			}
			b, err := readFile(path)
			if err != nil {
				return "", Error{name + "_FILE", "cannot read file"}
			}
			v = strings.TrimSuffix(string(b), "\n")
		}
		if v == "" {
			return "", Error{name, "must not be empty"}
		}
		return v, nil
	}
	var err error
	if c.BureauURL, err = required("TELESCREEN_URL"); err != nil {
		return c, err
	}
	if c.EnrollmentToken, err = secret("TELESCREEN_POLICE_ENROLLMENT_TOKEN"); err != nil {
		return c, err
	}
	key, err := secret("TELESCREEN_POLICE_FINGERPRINT_KEY")
	if err != nil {
		return c, err
	}
	if len(key) < 32 {
		return c, Error{"TELESCREEN_POLICE_FINGERPRINT_KEY", "must contain at least 32 bytes"}
	}
	c.FingerprintKey = []byte(key)
	if c.ServiceAccountFile, err = required("TELESCREEN_GOOGLE_SERVICE_ACCOUNT_FILE"); err != nil {
		return c, err
	}
	if v, ok := lookup("TELESCREEN_GOOGLE_DELEGATED_ADMIN"); ok && v != "" {
		c.DelegatedAdmin = &v
	}
	if v, ok := lookup("TELESCREEN_INTERNAL_DOMAINS"); ok {
		for _, part := range strings.Split(v, ",") {
			part = strings.ToLower(strings.TrimSpace(part))
			if part != "" {
				c.InternalDomains = append(c.InternalDomains, part)
			}
		}
	}
	integer := func(name string, fallback, min, max int) (int, error) {
		v, ok := lookup(name)
		if !ok {
			return fallback, nil
		}
		n, e := strconv.Atoi(v)
		if e != nil || n < min || n > max {
			return 0, Error{name, "out of range or not an integer"}
		}
		return n, nil
	}
	if c.ScanMaxBytes, err = integer("TELESCREEN_SCAN_MAX_BYTES", 5242880, 1, int(^uint(0)>>1)); err != nil {
		return c, err
	}
	if c.SweepShardSize, err = integer("TELESCREEN_SWEEP_SHARD_SIZE", 100, 1, 1000); err != nil {
		return c, err
	}
	if c.Concurrency, err = integer("TELESCREEN_POLICE_CONCURRENCY", 4, 1, 64); err != nil {
		return c, err
	}
	c.Name = "telescreen-gdrive-police"
	if v, ok := lookup("TELESCREEN_POLICE_NAME"); ok {
		c.Name = v
	}
	if len([]rune(c.Name)) < 1 || len([]rune(c.Name)) > 100 {
		return c, Error{"TELESCREEN_POLICE_NAME", "must be 1 to 100 characters"}
	}
	labels := "police=gdrive"
	if v, ok := lookup("TELESCREEN_POLICE_LABELS"); ok {
		labels = v
	}
	labelPattern := regexp.MustCompile(`^[a-z0-9_.-]+=[A-Za-z0-9_.:/-]+$`)
	for _, label := range strings.Split(labels, ",") {
		label = strings.TrimSpace(label)
		if !labelPattern.MatchString(label) {
			return c, Error{"TELESCREEN_POLICE_LABELS", "invalid label"}
		}
		c.Labels = append(c.Labels, label)
	}
	if v, ok := lookup("TELESCREEN_POLICE_ALLOW_HTTP"); ok {
		if v != "true" {
			return c, Error{"TELESCREEN_POLICE_ALLOW_HTTP", "must be true or unset"}
		}
		c.AllowHTTP = true
	}
	bases := []struct {
		name, fallback string
		target         *string
	}{{"TELESCREEN_URL", c.BureauURL, &c.BureauURL}, {"TELESCREEN_GOOGLE_API_BASE_URL", "https://www.googleapis.com", &c.GoogleAPIBaseURL}, {"TELESCREEN_GOOGLE_SHEETS_BASE_URL", "https://sheets.googleapis.com", &c.SheetsBaseURL}, {"TELESCREEN_GOOGLE_ADMIN_BASE_URL", "https://admin.googleapis.com", &c.AdminBaseURL}}
	for _, base := range bases {
		v := base.fallback
		if x, ok := lookup(base.name); ok {
			v = x
		}
		u, e := url.Parse(v)
		if e != nil || u.Hostname() == "" || (u.Scheme != "https" && !(c.AllowHTTP && u.Scheme == "http")) {
			return c, Error{base.name, "must be a valid allowed URL with host"}
		}
		*base.target = v
	}
	return c, nil
}
