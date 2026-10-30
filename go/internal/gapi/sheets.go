package gapi

import (
	"context"
	"net/http"
	"net/url"

	"github.com/tacogips/telescreen/go/internal/googleauth"
)

const sheetsScope = "https://www.googleapis.com/auth/spreadsheets.readonly"

type sheetsClient struct{ client client }

func NewSheets(httpClient *http.Client, tokens *googleauth.TokenSource, baseURL string) Sheets {
	return &sheetsClient{client: client{httpClient: httpClient, tokens: tokens, baseURL: baseURL, scope: sheetsScope}}
}

func (s *sheetsClient) SheetTitles(ctx context.Context, id string, subject *string) ([]string, error) {
	query := url.Values{"fields": {"sheets(properties(title))"}}
	resp, err := s.client.get(ctx, "/v4/spreadsheets/"+url.PathEscape(id), query, subject)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var result struct {
		Sheets []struct {
			Properties struct {
				Title string `json:"title"`
			} `json:"properties"`
		} `json:"sheets"`
	}
	if err := decodeJSON(resp, &result); err != nil {
		return nil, err
	}
	titles := make([]string, 0, len(result.Sheets))
	for _, sheet := range result.Sheets {
		titles = append(titles, sheet.Properties.Title)
	}
	return titles, nil
}

func (s *sheetsClient) ValuesBatchGet(ctx context.Context, id string, ranges []string, subject *string) ([]SheetValues, error) {
	query := url.Values{"ranges": ranges, "valueRenderOption": {"FORMATTED_VALUE"}}
	resp, err := s.client.get(ctx, "/v4/spreadsheets/"+url.PathEscape(id)+"/values:batchGet", query, subject)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var result struct {
		ValueRanges []SheetValues `json:"valueRanges"`
	}
	if err := decodeJSON(resp, &result); err != nil {
		return nil, err
	}
	for i := range result.ValueRanges {
		if result.ValueRanges[i].Values == nil {
			result.ValueRanges[i].Values = [][]CellValue{}
		}
	}
	return result.ValueRanges, nil
}
