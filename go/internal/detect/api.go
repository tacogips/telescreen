package detect

type RuleClass string

const (
	RuleCredential  RuleClass = "credential"
	RuleToken       RuleClass = "token"
	RulePrivateKey  RuleClass = "private_key"
	RulePassword    RuleClass = "password"
	RulePaymentCard RuleClass = "payment_card"
	RuleNationalID  RuleClass = "national_id"
	RuleEmail       RuleClass = "email"
	RulePhone       RuleClass = "phone"
	RuleAddress     RuleClass = "address"
)

type Span struct{ Start, End int }
type RawFinding struct {
	RuleID     string
	RuleClass  RuleClass
	Span       Span
	Confidence float64
}
type Finding struct {
	RuleID                  string
	RuleClass               RuleClass
	MaskedSnippet, Location string
	Confidence              float64
	ExternalFingerprint     string
}
type Scanner interface {
	ScanText(text string) []RawFinding
}
type Engine interface {
	ScanSegment(text, segmentLocator string) (findings []Finding, ok bool)
	HasHits(s string) bool
}
