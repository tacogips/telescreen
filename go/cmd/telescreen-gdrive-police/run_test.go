package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestRunExitCodes(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	privateKey := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(key)})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusUnauthorized) }))
	defer server.Close()
	values := map[string]string{"TELESCREEN_URL": server.URL, "TELESCREEN_POLICE_ENROLLMENT_TOKEN": "private-enrollment-value", "TELESCREEN_POLICE_FINGERPRINT_KEY": strings.Repeat("x", 32), "TELESCREEN_GOOGLE_SERVICE_ACCOUNT_FILE": "key", "TELESCREEN_POLICE_ALLOW_HTTP": "true"}
	lookup := func(name string) (string, bool) { v, ok := values[name]; return v, ok }
	tokenURI := "https://example.invalid/token"
	read := func(string) ([]byte, error) {
		return json.Marshal(map[string]string{"client_email": "worker@example.invalid", "private_key": string(privateKey), "token_uri": tokenURI})
	}
	var out bytes.Buffer
	delete(values, "TELESCREEN_URL")
	if got := run(context.Background(), lookup, read, &out); got != 2 || !strings.Contains(out.String(), "TELESCREEN_URL") {
		t.Fatalf("missing URL: %d %s", got, out.String())
	}
	values["TELESCREEN_URL"] = server.URL
	delete(values, "TELESCREEN_POLICE_ALLOW_HTTP")
	values["TELESCREEN_URL"] = "https://example.invalid"
	tokenURI = "http://example.invalid/token"
	out.Reset()
	if got := run(context.Background(), lookup, read, &out); got != 2 {
		t.Fatalf("insecure token URI: %d", got)
	}
	values["TELESCREEN_URL"] = server.URL
	values["TELESCREEN_POLICE_ALLOW_HTTP"] = "true"
	out.Reset()
	if got := run(context.Background(), lookup, read, &out); got != 3 {
		t.Fatalf("unauthorized: %d", got)
	}
	if strings.Contains(out.String(), values["TELESCREEN_POLICE_ENROLLMENT_TOKEN"]) {
		t.Fatal("enrollment token leaked")
	}
}
