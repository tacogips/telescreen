package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"time"

	"github.com/tacogips/telescreen/go/internal/config"
	"github.com/tacogips/telescreen/go/internal/detect"
	"github.com/tacogips/telescreen/go/internal/gapi"
	"github.com/tacogips/telescreen/go/internal/gdrive"
	"github.com/tacogips/telescreen/go/internal/googleauth"
	"github.com/tacogips/telescreen/go/internal/police"
	"github.com/tacogips/telescreen/go/internal/pytext"
)

func run(ctx context.Context, lookup func(string) (string, bool), readFile func(string) ([]byte, error), stderr io.Writer) int {
	cfg, err := config.Load(lookup, readFile)
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 2
	}
	data, err := readFile(cfg.ServiceAccountFile)
	if err != nil {
		fmt.Fprintln(stderr, "invalid configuration: TELESCREEN_GOOGLE_SERVICE_ACCOUNT_FILE")
		return 2
	}
	key, err := googleauth.ParseServiceAccountKey(data)
	if err != nil {
		fmt.Fprintln(stderr, "invalid configuration: TELESCREEN_GOOGLE_SERVICE_ACCOUNT_FILE")
		return 2
	}
	u, err := url.Parse(key.TokenURI)
	if err != nil || u.Hostname() == "" || (u.Scheme != "https" && !(cfg.AllowHTTP && u.Scheme == "http")) {
		fmt.Fprintln(stderr, "invalid configuration: TELESCREEN_GOOGLE_SERVICE_ACCOUNT_FILE")
		return 2
	}
	tables, err := pytext.Default()
	if err != nil {
		fmt.Fprintln(stderr, "unicode tables unavailable")
		return 1
	}
	detector := detect.NewDetector(tables)
	engine := detect.NewEngine(detector, tables, cfg.FingerprintKey)
	httpClient := &http.Client{}
	tokens := googleauth.NewTokenSource(httpClient, key, time.Now)
	drive := gapi.NewDrive(httpClient, tokens, cfg.GoogleAPIBaseURL)
	sheets := gapi.NewSheets(httpClient, tokens, cfg.SheetsBaseURL)
	var admin gapi.Admin
	if cfg.DelegatedAdmin != nil {
		admin = gapi.NewAdmin(httpClient, tokens, cfg.AdminBaseURL, *cfg.DelegatedAdmin)
	}
	logger := slog.New(slog.NewJSONHandler(stderr, nil))
	service := gdrive.NewService(gdrive.Config{InternalDomains: cfg.InternalDomains, MaxBytes: cfg.ScanMaxBytes, ShardSize: cfg.SweepShardSize}, drive, sheets, admin, engine, tables, logger)
	client := police.NewClient(cfg.BureauURL, httpClient)
	manifest := police.Manifest{Name: cfg.Name, Version: "0.1.0", ContractVersion: police.ContractVersion, Mode: "pull", TaskTypes: []string{police.TaskDetect, police.TaskPartition, police.TaskScanShard}, Labels: cfg.Labels, Concurrency: cfg.Concurrency}
	registration, err := client.Register(ctx, manifest, cfg.EnrollmentToken)
	if err != nil {
		return exitCode(err)
	}
	return exitCode(police.NewWorker(client, registration, service.Handlers(), cfg.Concurrency, logger, time.Now).Run(ctx))
}

func exitCode(err error) int {
	if err == nil {
		return 0
	}
	var bureau *police.BureauError
	if errors.As(err, &bureau) && (bureau.Status == http.StatusUnauthorized || bureau.Status == http.StatusForbidden) {
		return 3
	}
	return 1
}
