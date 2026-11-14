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
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestTokenClaimsSignatureAndCache(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	current := time.Unix(1700000000, 0)
	calls := 0
	var endpoint string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Method != http.MethodPost || r.FormValue("grant_type") != "urn:ietf:params:oauth:grant-type:jwt-bearer" {
			t.Error("incorrect request")
		}
		parts := strings.Split(r.FormValue("assertion"), ".")
		if len(parts) != 3 {
			t.Error("bad assertion")
			w.WriteHeader(500)
			return
		}
		h := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
		sig, e := base64.RawURLEncoding.DecodeString(parts[2])
		if e != nil || rsa.VerifyPKCS1v15(&key.PublicKey, crypto.SHA256, h[:], sig) != nil {
			t.Error("signature invalid")
		}
		header, _ := base64.RawURLEncoding.DecodeString(parts[0])
		if string(header) != `{"alg":"RS256","typ":"JWT"}` {
			t.Error("header invalid")
		}
		payload, _ := base64.RawURLEncoding.DecodeString(parts[1])
		var claims struct {
			Iss   string  `json:"iss"`
			Scope string  `json:"scope"`
			Aud   string  `json:"aud"`
			Iat   int64   `json:"iat"`
			Exp   int64   `json:"exp"`
			Sub   *string `json:"sub"`
		}
		if json.Unmarshal(payload, &claims) != nil {
			t.Error("bad claims")
		}
		if claims.Iss != "account@example.invalid" || claims.Scope != "scope-a scope-b" || claims.Aud != endpoint || claims.Iat != current.Unix() || claims.Exp != current.Unix()+3600 {
			t.Error("claims incorrect")
		}
		if calls <= 2 && claims.Sub != nil {
			t.Error("unexpected subject")
		}
		if calls == 3 && (claims.Sub == nil || *claims.Sub != "user@example.invalid") {
			t.Error("subject missing")
		}
		fmt.Fprint(w, `{"access_token":"sample","expires_in":3600}`)
	}))
	defer server.Close()
	endpoint = server.URL
	for _, format := range []string{"PKCS1", "PKCS8"} {
		var der []byte
		if format == "PKCS1" {
			der = x509.MarshalPKCS1PrivateKey(key)
		} else {
			der, err = x509.MarshalPKCS8PrivateKey(key)
			if err != nil {
				t.Fatal(err)
			}
		}
		encoded, _ := json.Marshal(struct {
			Email   string `json:"client_email"`
			Private string `json:"private_key"`
			URI     string `json:"token_uri"`
		}{"account@example.invalid", string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})), endpoint})
		parsed, e := ParseServiceAccountKey(encoded)
		if e != nil {
			t.Fatal(e)
		}
		if format == "PKCS8" {
			continue
		}
		source := NewTokenSource(server.Client(), parsed, func() time.Time { return current })
		scopes := []string{"scope-a", "scope-b"}
		for i := 0; i < 2; i++ {
			token, e := source.Token(context.Background(), scopes, nil)
			if e != nil || token != "sample" {
				t.Fatal("token failed", e)
			}
		}
		if calls != 1 {
			t.Fatal("cache miss")
		}
		current = current.Add(3541 * time.Second)
		if _, e := source.Token(context.Background(), scopes, nil); e != nil || calls != 2 {
			t.Fatal("refresh failed", e)
		}
		subject := "user@example.invalid"
		if _, e := source.Token(context.Background(), scopes, &subject); e != nil || calls != 3 {
			t.Fatal("subject request failed", e)
		}
	}
}

func TestBadKeyAndTokenFailure(t *testing.T) {
	_, err := ParseServiceAccountKey([]byte(`{"client_email":"x","token_uri":"x","private_key":"not-a-pem"}`))
	if err == nil || strings.Contains(err.Error(), "not-a-pem") {
		t.Fatal("unsafe key error")
	}
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusInternalServerError) }))
	defer server.Close()
	source := NewTokenSource(server.Client(), ServiceAccountKey{ClientEmail: "x", TokenURI: server.URL, key: key}, time.Now)
	_, err = source.Token(context.Background(), []string{"scope"}, nil)
	if _, ok := err.(TokenError); !ok {
		t.Fatalf("expected TokenError, got %T", err)
	}
}
