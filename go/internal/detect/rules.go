package detect

import "github.com/tacogips/telescreen/go/internal/pytext"

type matcher func([]rune, int, *pytext.Tables) (int, int, int, bool)
type rule struct {
	id            string
	class         RuleClass
	confidence    float64
	keywords      []string
	keywordWindow int
	validate      func([]rune) bool
	match         matcher
}

func newRules(t *pytext.Tables) []rule {
	return []rule{
		{"aws_access_key_id", RuleCredential, .99, nil, 0, nil, matchAwsAccessKeyID},
		{"aws_secret_access_key", RuleCredential, .92, []string{"aws_secret", "secret_access_key"}, 80, func(v []rune) bool { return shannonEntropy(v) >= 4 }, matchAwsSecretAccessKey},
		{"github_token", RuleToken, .99, nil, 0, nil, matchGithubToken},
		{"slack_token", RuleToken, .98, nil, 0, nil, matchSlackToken},
		{"google_api_key", RuleCredential, .99, nil, 0, nil, matchGoogleAPIKey},
		{"private_key", RulePrivateKey, 1, nil, 0, nil, matchPrivateKey},
		{"jwt", RuleToken, .95, nil, 0, jwtHeaderValid, matchJWT},
		{"password_assignment", RulePassword, .95, nil, 0, func(v []rune) bool { return len(v) >= 8 && !isPlaceholder(v, t) }, matchPasswordAssignment},
		{"credit_card", RulePaymentCard, .985, nil, 0, func(v []rune) bool {
			digits := make([]rune, 0, len(v))
			for _, r := range v {
				if _, ok := t.DecimalValue(r); ok {
					digits = append(digits, r)
				}
			}
			return luhnValid(digits, t) && knownCardIIN(digits, t)
		}, matchCard},
		{"my_number", RuleNationalID, .98, nil, 0, func(v []rune) bool { return myNumberValid(v, t) }, matchMyNumber},
		{"email", RuleEmail, .9, nil, 0, nil, matchEmail},
		{"phone_jp", RulePhone, .8, nil, 0, nil, matchPhoneJP},
		{"phone_intl", RulePhone, .78, nil, 0, nil, matchPhoneIntl},
		{"address_jp", RuleAddress, .6, nil, 0, nil, matchAddressJP},
		{"address_en", RuleAddress, .5, nil, 0, nil, matchAddressEN},
	}
}
func findAll(text []rune, t *pytext.Tables, m matcher, visit func(int, int)) {
	for i := 0; i <= len(text); {
		s, e, end, ok := m(text, i, t)
		if !ok {
			i++
			continue
		}
		visit(s, e)
		if end <= i {
			i++
		} else {
			i = end
		}
	}
}
