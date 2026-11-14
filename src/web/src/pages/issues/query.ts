import {
  ISSUE_QUERY_KEYS,
  tokenizeIssueQuery,
  validateIssueTokens,
} from "../../api/issueQuery";
import type { QueryToken } from "../../api/types";

export { ISSUE_QUERY_KEYS, tokenizeIssueQuery, validateIssueTokens };

export function issueQueryTokens(query: string): QueryToken[] {
  return tokenizeIssueQuery(query);
}
