package gdrive

import (
	"bytes"
	"encoding/json"
	"io"
)

type selector struct {
	Kind      string   `json:"kind"`
	DriveIDs  []string `json:"drive_ids,omitempty"`
	FolderIDs []string `json:"folder_ids,omitempty"`
	MimeTypes []string `json:"mime_types,omitempty"`
}

func parseSelector(raw json.RawMessage) (selector, bool) {
	var v selector
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if d.Decode(&v) != nil {
		return v, false
	}
	var extra interface{}
	if d.Decode(&extra) != io.EOF {
		return v, false
	}
	if v.Kind != "gdrive" && v.Kind != "gdocs" && v.Kind != "gsheets" {
		return v, false
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil {
		return v, false
	}
	for _, name := range []string{"drive_ids", "folder_ids", "mime_types"} {
		if value, present := fields[name]; present && !stringArray(value) {
			return v, false
		}
	}
	return v, true
}

func stringArray(raw json.RawMessage) bool {
	var values []json.RawMessage
	if json.Unmarshal(raw, &values) != nil || values == nil {
		return false
	}
	for _, value := range values {
		var text *string
		if json.Unmarshal(value, &text) != nil || text == nil {
			return false
		}
	}
	return true
}
