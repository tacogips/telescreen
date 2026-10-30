package gdrive

import (
	"context"
	"errors"
	"unicode/utf8"

	"github.com/tacogips/telescreen/go/internal/gapi"
	"github.com/tacogips/telescreen/go/internal/police"
)

type fetchState int

const (
	fetchComplete fetchState = iota
	fetchRemoved
	fetchFailed
	fetchRateLimited
)

type fetchOutcome struct {
	state      fetchState
	locator    string
	findings   []police.MaskedFinding
	exposure   *police.ResourceExposure
	inventory  *police.ResourceInventory
	bytes      int
	retryAfter *float64
}

func fetchError(locator string, err error) fetchOutcome {
	var gone gapi.GoneError
	var rate gapi.RateLimitedError
	if errors.As(err, &gone) {
		return fetchOutcome{state: fetchRemoved, locator: locator}
	}
	if errors.As(err, &rate) {
		return fetchOutcome{state: fetchRateLimited, locator: locator, retryAfter: rate.RetryAfterSeconds}
	}
	return fetchOutcome{state: fetchFailed, locator: locator}
}

func (s *Service) fetch(ctx context.Context, sel selector, id string, subject *string) fetchOutcome {
	locator := sel.Kind + ":" + id
	failed := fetchOutcome{state: fetchFailed, locator: locator}
	if s.engine.HasHits(locator) {
		return failed
	}
	file, err := s.drive.FilesGetMeta(ctx, id, subject)
	if err != nil {
		return fetchError(locator, err)
	}
	if file.Trashed {
		return fetchOutcome{state: fetchRemoved, locator: locator}
	}
	if !matchFile(file, sel.Kind, sel.DriveIDs, sel.FolderIDs, sel.MimeTypes) {
		return failed
	}
	var exposure *police.ResourceExposure
	permissions, err := s.drive.ListPermissions(ctx, id, subject)
	if err != nil {
		result := fetchError(locator, err)
		if result.state != fetchFailed {
			return result
		}
	} else {
		e := driveExposure(permissions, file.DriveID, s.cfg.InternalDomains, s.tables)
		e.Locator = locator
		exposure = &e
	}
	result := fetchOutcome{state: fetchComplete, locator: locator, exposure: exposure,
		inventory: &police.ResourceInventory{Locator: locator, Kind: "file"}}
	if sel.Kind == "gdrive" && !textLike(file.MimeType) {
		return result
	}
	var segments []segment
	if sel.Kind == "gsheets" {
		titles, err := s.sheets.SheetTitles(ctx, id, subject)
		if err != nil {
			return fetchError(locator, err)
		}
		values, err := s.sheets.ValuesBatchGet(ctx, id, sheetRanges(titles), subject)
		if err != nil {
			return fetchError(locator, err)
		}
		var count int
		segments, count = sheetSegments(values)
		result.inventory.Kind = "table"
		result.inventory.ItemCount = &count
		for _, seg := range segments {
			if !utf8.ValidString(seg.Text) {
				return failed
			}
			result.bytes += len(seg.Text)
		}
		if result.bytes > s.cfg.MaxBytes {
			return failed
		}
	} else {
		var payload []byte
		var oversized bool
		if sel.Kind == "gdocs" {
			payload, oversized, err = s.drive.Export(ctx, id, "text/plain", subject, s.cfg.MaxBytes)
		} else {
			payload, oversized, err = s.drive.Download(ctx, id, subject, s.cfg.MaxBytes)
		}
		if err != nil {
			return fetchError(locator, err)
		}
		if oversized || len(payload) > s.cfg.MaxBytes || !utf8.Valid(payload) {
			return failed
		}
		result.bytes = len(payload)
		segments = lineSegments(string(payload), s.tables)
	}
	for _, seg := range segments {
		findings, ok := s.engine.ScanSegment(seg.Text, seg.Locator)
		if !ok {
			return failed
		}
		for _, f := range findings {
			result.findings = append(result.findings, police.MaskedFinding{
				Locator: locator, RuleID: f.RuleID, RuleClass: string(f.RuleClass),
				MaskedSnippet: f.MaskedSnippet, Location: f.Location,
				Confidence: f.Confidence, ExternalFingerprint: f.ExternalFingerprint,
			})
		}
	}
	return result
}
