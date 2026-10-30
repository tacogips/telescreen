package gapi

import (
	"context"
	"io"
	"net/http"
	"net/url"
	"strconv"

	"github.com/tacogips/telescreen/go/internal/googleauth"
)

const driveScope = "https://www.googleapis.com/auth/drive.readonly"
const fileFields = "id,name,mimeType,trashed,driveId,parents,owners(emailAddress),modifiedTime,size"

type driveClient struct{ client client }

func NewDrive(httpClient *http.Client, tokens *googleauth.TokenSource, baseURL string) Drive {
	return &driveClient{client{httpClient: httpClient, tokens: tokens, baseURL: baseURL, scope: driveScope}}
}

func (d *driveClient) FilesList(ctx context.Context, p FilesListParams) (FileListPage, error) {
	pageSize := p.PageSize
	if pageSize > 1000 {
		pageSize = 1000
	}
	q := url.Values{"q": {p.Q}, "corpora": {p.Corpora}, "pageSize": {strconv.Itoa(pageSize)}, "supportsAllDrives": {"true"}, "includeItemsFromAllDrives": {"true"}, "fields": {"files(" + fileFields + "),nextPageToken"}}
	if p.DriveID != nil {
		q.Set("driveId", *p.DriveID)
	}
	if p.PageToken != nil {
		q.Set("pageToken", *p.PageToken)
	}
	resp, err := d.client.get(ctx, "/drive/v3/files", q, p.Subject)
	if err != nil {
		return FileListPage{}, err
	}
	defer resp.Body.Close()
	var page FileListPage
	if err := decodeJSON(resp, &page); err != nil {
		return FileListPage{}, err
	}
	return page, nil
}

func (d *driveClient) FilesGetMeta(ctx context.Context, id string, subject *string) (DriveFile, error) {
	resp, err := d.client.get(ctx, "/drive/v3/files/"+url.PathEscape(id), url.Values{"fields": {fileFields}}, subject)
	if err != nil {
		return DriveFile{}, err
	}
	defer resp.Body.Close()
	var file DriveFile
	if err := decodeJSON(resp, &file); err != nil {
		return DriveFile{}, err
	}
	return file, nil
}

func (d *driveClient) ListPermissions(ctx context.Context, id string, subject *string) ([]DrivePermission, error) {
	var permissions []DrivePermission
	var token *string
	for {
		q := url.Values{"supportsAllDrives": {"true"}, "fields": {"permissions(type,role,domain,emailAddress,allowFileDiscovery),nextPageToken"}}
		if token != nil {
			q.Set("pageToken", *token)
		}
		resp, err := d.client.get(ctx, "/drive/v3/files/"+url.PathEscape(id)+"/permissions", q, subject)
		if err != nil {
			return nil, err
		}
		var page struct {
			Permissions   []DrivePermission `json:"permissions"`
			NextPageToken *string           `json:"nextPageToken"`
		}
		err = decodeJSON(resp, &page)
		resp.Body.Close()
		if err != nil {
			return nil, err
		}
		permissions = append(permissions, page.Permissions...)
		token = page.NextPageToken
		if token == nil || *token == "" {
			return permissions, nil
		}
	}
}

func (d *driveClient) media(ctx context.Context, path string, q url.Values, subject *string, maxBytes int) ([]byte, bool, error) {
	if maxBytes < 0 {
		return nil, false, FetchFailedError{}
	}
	resp, err := d.client.get(ctx, path, q, subject)
	if err != nil {
		return nil, false, err
	}
	defer resp.Body.Close()
	// Avoid overflowing the limit for the largest possible int.
	limit := int64(maxBytes)
	if limit < int64(^uint64(0)>>1) {
		limit++
	}
	content, err := io.ReadAll(io.LimitReader(resp.Body, limit))
	if err != nil {
		return nil, false, FetchFailedError{}
	}
	if len(content) > maxBytes {
		return nil, true, nil
	}
	return content, false, nil
}

func (d *driveClient) Download(ctx context.Context, id string, subject *string, maxBytes int) ([]byte, bool, error) {
	return d.media(ctx, "/drive/v3/files/"+url.PathEscape(id), url.Values{"alt": {"media"}}, subject, maxBytes)
}

func (d *driveClient) Export(ctx context.Context, id, mime string, subject *string, maxBytes int) ([]byte, bool, error) {
	return d.media(ctx, "/drive/v3/files/"+url.PathEscape(id)+"/export", url.Values{"mimeType": {mime}}, subject, maxBytes)
}

func (d *driveClient) DrivesList(ctx context.Context, pageToken *string, subject string) (DrivesPage, error) {
	q := url.Values{"pageSize": {"100"}, "fields": {"drives(id,name),nextPageToken"}}
	if pageToken != nil {
		q.Set("pageToken", *pageToken)
	}
	resp, err := d.client.get(ctx, "/drive/v3/drives", q, &subject)
	if err != nil {
		return DrivesPage{}, err
	}
	defer resp.Body.Close()
	var page DrivesPage
	if err := decodeJSON(resp, &page); err != nil {
		return DrivesPage{}, err
	}
	return page, nil
}
