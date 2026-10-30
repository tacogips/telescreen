package googleauth

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

type ServiceAccountKey struct {
	ClientEmail string
	TokenURI    string
	key         *rsa.PrivateKey
}

func ParseServiceAccountKey(data []byte) (ServiceAccountKey, error) {
	var input struct {
		ClientEmail string `json:"client_email"`
		PrivateKey  string `json:"private_key"`
		TokenURI    string `json:"token_uri"`
	}
	invalid := errors.New("google service-account key is invalid")
	if json.Unmarshal(data, &input) != nil || input.ClientEmail == "" || input.TokenURI == "" {
		return ServiceAccountKey{}, invalid
	}
	block, _ := pem.Decode([]byte(input.PrivateKey))
	if block == nil {
		return ServiceAccountKey{}, invalid
	}
	var key *rsa.PrivateKey
	if parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes); err == nil {
		key, _ = parsed.(*rsa.PrivateKey)
	} else {
		key, _ = x509.ParsePKCS1PrivateKey(block.Bytes)
	}
	if key == nil || key.Validate() != nil {
		return ServiceAccountKey{}, invalid
	}
	return ServiceAccountKey{ClientEmail: input.ClientEmail, TokenURI: input.TokenURI, key: key}, nil
}

type TokenError struct{}

func (TokenError) Error() string { return "google token request failed" }

type cacheKey struct {
	scopes     string
	subject    string
	hasSubject bool
}
type cacheEntry struct {
	token   string
	expires time.Time
}
type TokenSource struct {
	client *http.Client
	key    ServiceAccountKey
	now    func() time.Time
	mu     sync.Mutex
	cache  map[cacheKey]cacheEntry
}

func NewTokenSource(httpClient *http.Client, key ServiceAccountKey, now func() time.Time) *TokenSource {
	return &TokenSource{client: httpClient, key: key, now: now, cache: make(map[cacheKey]cacheEntry)}
}

func (t *TokenSource) Token(ctx context.Context, scopes []string, subject *string) (string, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	now := t.now()
	k := cacheKey{scopes: strings.Join(scopes, " "), hasSubject: subject != nil}
	if subject != nil {
		k.subject = *subject
	}
	for key, entry := range t.cache {
		if !entry.expires.After(now) {
			delete(t.cache, key)
		}
	}
	if entry, ok := t.cache[k]; ok && entry.expires.After(now.Add(60*time.Second)) {
		return entry.token, nil
	}
	if t.key.key == nil {
		return "", TokenError{}
	}
	claims := struct {
		Iss   string  `json:"iss"`
		Scope string  `json:"scope"`
		Aud   string  `json:"aud"`
		Iat   int64   `json:"iat"`
		Exp   int64   `json:"exp"`
		Sub   *string `json:"sub,omitempty"`
	}{t.key.ClientEmail, k.scopes, t.key.TokenURI, now.Unix(), now.Unix() + 3600, subject}
	payload, err := json.Marshal(claims)
	if err != nil {
		return "", TokenError{}
	}
	encode := base64.RawURLEncoding.EncodeToString
	assertion := encode([]byte(`{"alg":"RS256","typ":"JWT"}`)) + "." + encode(payload)
	hash := sha256.Sum256([]byte(assertion))
	signature, err := rsa.SignPKCS1v15(rand.Reader, t.key.key, crypto.SHA256, hash[:])
	if err != nil {
		return "", TokenError{}
	}
	form := url.Values{"grant_type": {"urn:ietf:params:oauth:grant-type:jwt-bearer"}, "assertion": {assertion + "." + encode(signature)}}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, t.key.TokenURI, strings.NewReader(form.Encode()))
	if err != nil {
		return "", TokenError{}
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	resp, err := t.client.Do(req)
	if err != nil {
		return "", TokenError{}
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", TokenError{}
	}
	var result struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int64  `json:"expires_in"`
	}
	if json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&result) != nil || result.AccessToken == "" || result.ExpiresIn <= 0 {
		return "", TokenError{}
	}
	t.cache[k] = cacheEntry{token: result.AccessToken, expires: now.Add(time.Duration(result.ExpiresIn) * time.Second)}
	return result.AccessToken, nil
}
