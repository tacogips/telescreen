package gdrive

import (
	"strings"

	"github.com/tacogips/telescreen/go/internal/gapi"
	"github.com/tacogips/telescreen/go/internal/police"
	"github.com/tacogips/telescreen/go/internal/pytext"
)

func driveExposure(perms []gapi.DrivePermission, driveID *string, domains []string, tables *pytext.Tables) police.ResourceExposure {
	normalized := make([]string, 0, len(domains))
	for _, domain := range domains {
		normalized = append(normalized, tables.CaseFold(tables.Lower(domain)))
	}
	out := police.ResourceExposure{Visibility: "private", SharedDrive: driveID != nil}
	for _, p := range perms {
		if p.Type == "anyone" && p.AllowFileDiscovery {
			out.Visibility = "public"
			return out
		}
	}
	for _, p := range perms {
		if p.Type == "anyone" {
			out.Visibility = "anyone_with_link"
			return out
		}
	}
	for _, p := range perms {
		if p.Role != "owner" && len(normalized) == 0 && (p.Type == "user" || p.Type == "group") {
			out.Visibility = "unknown"
			return out
		}
	}
	domainGrant, internalGrant := false, false
	for _, p := range perms {
		if p.Role == "owner" {
			continue
		}
		if p.Type == "domain" {
			if p.Domain != nil && contains(normalized, tables.CaseFold(*p.Domain)) {
				domainGrant = true
			} else {
				out.ExternalPrincipalCount++
			}
		} else if (p.Type == "user" || p.Type == "group") && p.EmailAddress != nil {
			idx := strings.LastIndex(*p.EmailAddress, "@")
			if idx >= 0 {
				if contains(normalized, tables.CaseFold((*p.EmailAddress)[idx+1:])) {
					internalGrant = true
				} else {
					out.ExternalPrincipalCount++
				}
			}
		}
	}
	if out.ExternalPrincipalCount > 0 {
		out.Visibility = "external_shared"
	} else if domainGrant {
		out.Visibility = "domain"
	} else if internalGrant {
		out.Visibility = "internal"
	}
	return out
}
