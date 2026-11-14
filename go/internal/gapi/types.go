package gapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
)

type DriveFile struct {
	ID       string   `json:"id"`
	Name     string   `json:"name"`
	MimeType string   `json:"mimeType"`
	Trashed  bool     `json:"trashed"`
	DriveID  *string  `json:"driveId"`
	Parents  []string `json:"parents"`
}
type DrivePermission struct {
	Type               string  `json:"type"`
	Role               string  `json:"role"`
	Domain             *string `json:"domain"`
	EmailAddress       *string `json:"emailAddress"`
	AllowFileDiscovery bool    `json:"allowFileDiscovery"`
}
type FileListPage struct {
	Files         []DriveFile `json:"files"`
	NextPageToken *string     `json:"nextPageToken"`
}
type DriveRef struct {
	ID string `json:"id"`
}
type DrivesPage struct {
	Drives        []DriveRef `json:"drives"`
	NextPageToken *string    `json:"nextPageToken"`
}
type DirectoryUser struct {
	ID           string `json:"id"`
	PrimaryEmail string `json:"primaryEmail"`
}
type UsersPage struct {
	Users         []DirectoryUser `json:"users"`
	NextPageToken *string         `json:"nextPageToken"`
}
type CellKind string

const (
	CellString CellKind = "string"
	CellNumber CellKind = "number"
	CellBool   CellKind = "bool"
)

type CellValue struct {
	Kind   CellKind
	Text   string
	Number string
	Bool   bool
}

func (c *CellValue) UnmarshalJSON(b []byte) error {
	var v interface{}
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	if err := d.Decode(&v); err != nil {
		return err
	}
	switch x := v.(type) {
	case string:
		*c = CellValue{Kind: CellString, Text: x}
	case json.Number:
		*c = CellValue{Kind: CellNumber, Number: string(x)}
	case bool:
		*c = CellValue{Kind: CellBool, Bool: x}
	default:
		return fmt.Errorf("unsupported cell value")
	}
	return nil
}

type SheetValues struct {
	Range  string        `json:"range"`
	Values [][]CellValue `json:"values"`
}
type FilesListParams struct {
	Q, Corpora         string
	DriveID, PageToken *string
	PageSize           int
	Subject            *string
}
type GoneError struct{}

func (GoneError) Error() string { return "gone" }

type ForbiddenError struct{}

func (ForbiddenError) Error() string { return "forbidden" }

type FetchFailedError struct{}

func (FetchFailedError) Error() string { return "fetch failed" }

type RateLimitedError struct{ RetryAfterSeconds *float64 }

func (RateLimitedError) Error() string { return "rate limited" }

type Drive interface {
	FilesList(context.Context, FilesListParams) (FileListPage, error)
	FilesGetMeta(context.Context, string, *string) (DriveFile, error)
	ListPermissions(context.Context, string, *string) ([]DrivePermission, error)
	Download(context.Context, string, *string, int) ([]byte, bool, error)
	Export(context.Context, string, string, *string, int) ([]byte, bool, error)
	DrivesList(context.Context, *string, string) (DrivesPage, error)
}
type Sheets interface {
	SheetTitles(context.Context, string, *string) ([]string, error)
	ValuesBatchGet(context.Context, string, []string, *string) ([]SheetValues, error)
}
type Admin interface {
	UsersList(context.Context, *string) (UsersPage, error)
	UserEmail(context.Context, string) (string, error)
	AdminSubject() string
}
