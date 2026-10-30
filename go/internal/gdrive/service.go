package gdrive

import (
	"github.com/tacogips/telescreen/go/internal/detect"
	"github.com/tacogips/telescreen/go/internal/gapi"
	"github.com/tacogips/telescreen/go/internal/police"
	"github.com/tacogips/telescreen/go/internal/pytext"
	"log/slog"
)

type Config struct {
	InternalDomains []string
	MaxBytes        int
	ShardSize       int
}
type Service struct {
	cfg    Config
	drive  gapi.Drive
	sheets gapi.Sheets
	admin  gapi.Admin
	engine detect.Engine
	tables *pytext.Tables
	logger *slog.Logger
}

func NewService(cfg Config, drive gapi.Drive, sheets gapi.Sheets, admin gapi.Admin, engine detect.Engine, tables *pytext.Tables, logger *slog.Logger) *Service {
	return &Service{cfg, drive, sheets, admin, engine, tables, logger}
}
func (s *Service) Handlers() map[string]police.Handler {
	return map[string]police.Handler{police.TaskDetect: s.detect, police.TaskPartition: s.partition, police.TaskScanShard: s.scanShard}
}
func completed(task police.TaskEnvelope) police.ResultEnvelope {
	return police.ResultEnvelope{ContractVersion: police.ContractVersion, TaskID: task.TaskID, Status: police.StatusCompleted}
}
func failed(task police.TaskEnvelope, code string, retry *float64) police.ResultEnvelope {
	r := completed(task)
	r.Status = police.StatusFailed
	r.Error = &police.ResultError{Code: code, Detail: code, RetryAfterSeconds: retry}
	return r
}
