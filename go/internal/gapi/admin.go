package gapi

import (
	"context"
	"net/http"
	"net/url"
	"sync"

	"github.com/tacogips/telescreen/go/internal/googleauth"
)

const adminScope = "https://www.googleapis.com/auth/admin.directory.user.readonly"

type adminClient struct {
	client  client
	subject string
	mu      sync.Mutex
	emails  map[string]string
}

func NewAdmin(httpClient *http.Client, tokens *googleauth.TokenSource, baseURL, adminSubject string) Admin {
	return &adminClient{client: client{httpClient: httpClient, tokens: tokens, baseURL: baseURL, scope: adminScope}, subject: adminSubject, emails: make(map[string]string)}
}

func (a *adminClient) AdminSubject() string { return a.subject }

func (a *adminClient) UsersList(ctx context.Context, pageToken *string) (UsersPage, error) {
	query := url.Values{"customer": {"my_customer"}, "maxResults": {"500"}}
	if pageToken != nil {
		query.Set("pageToken", *pageToken)
	}
	resp, err := a.client.get(ctx, "/admin/directory/v1/users", query, &a.subject)
	if err != nil {
		return UsersPage{}, err
	}
	defer resp.Body.Close()
	var page UsersPage
	if err := decodeJSON(resp, &page); err != nil {
		return UsersPage{}, err
	}
	return page, nil
}

func (a *adminClient) UserEmail(ctx context.Context, id string) (string, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if email, ok := a.emails[id]; ok {
		return email, nil
	}
	query := url.Values{"fields": {"primaryEmail"}}
	resp, err := a.client.get(ctx, "/admin/directory/v1/users/"+url.PathEscape(id), query, &a.subject)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	var user DirectoryUser
	if err := decodeJSON(resp, &user); err != nil {
		return "", err
	}
	a.emails[id] = user.PrimaryEmail
	return user.PrimaryEmail, nil
}
