package gdrive

import (
	"strings"

	"github.com/tacogips/telescreen/go/internal/gapi"
)

func contains(values []string, value string) bool {
	for _, v := range values {
		if v == value {
			return true
		}
	}
	return false
}

func textLike(mime string) bool {
	return strings.HasPrefix(mime, "text/") || contains([]string{"application/json", "application/csv", "application/yaml", "application/xml"}, mime) || strings.HasSuffix(mime, "+xml") || strings.HasSuffix(mime, "+json")
}

func matchFile(file gapi.DriveFile, kind string, driveIDs, folderIDs, mimeTypes []string) bool {
	if kind == "gdocs" && file.MimeType != "application/vnd.google-apps.document" {
		return false
	}
	if kind == "gsheets" && file.MimeType != "application/vnd.google-apps.spreadsheet" {
		return false
	}
	if kind == "gdrive" && !textLike(file.MimeType) && !strings.HasPrefix(file.MimeType, "image/") && len(mimeTypes) == 0 {
		return false
	}
	if len(mimeTypes) > 0 && !contains(mimeTypes, file.MimeType) {
		return false
	}
	if len(driveIDs) == 0 && len(folderIDs) == 0 {
		return true
	}
	if file.DriveID != nil && contains(driveIDs, *file.DriveID) {
		return true
	}
	for _, parent := range file.Parents {
		if contains(folderIDs, parent) {
			return true
		}
	}
	return false
}

func included(file gapi.DriveFile, corpus string, driveIDs, folderIDs []string) bool {
	if strings.HasPrefix(corpus, "user:") {
		return true
	}
	if strings.HasPrefix(corpus, "drive:") {
		return file.DriveID != nil && *file.DriveID == strings.TrimPrefix(corpus, "drive:")
	}
	if file.DriveID != nil && contains(driveIDs, *file.DriveID) {
		return false
	}
	folder := strings.TrimPrefix(corpus, "folder:")
	if !contains(folderIDs, folder) {
		return true
	}
	for _, earlier := range folderIDs {
		if earlier == folder {
			break
		}
		if contains(file.Parents, earlier) {
			return false
		}
	}
	return true
}
