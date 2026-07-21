export const REDACTED_PRESENTATION_VALUE = "[Redacted]";

const AUTHORIZATION_PATTERN =
  /\b(?:authorization|proxy-authorization)\s*[:=]\s*(?:bearer\s+)?[^\s,;]+/gi;
const CREDENTIAL_ASSIGNMENT_PATTERN =
  /\b(?:api[_-]?key|access[_-]?key|client[_-]?secret|refresh[_-]?token|session[_-]?(?:key|token)|token|secret|password|passphrase|cookie|credential)\b\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi;
const QUERY_CREDENTIAL_PATTERN =
  /([?&](?:api[_-]?key|access[_-]?key|client[_-]?secret|refresh[_-]?token|session[_-]?(?:key|token)|token|secret|password|passphrase|signature)=)[^&#\s]+/gi;
const URL_USERINFO_PATTERN = /(https?:\/\/)[^/@\s]+@/gi;
const BARE_BEARER_PATTERN =
  /\bbearer\s+[A-Za-z0-9._~+/-]{12,}={0,2}(?=\s|$|[,;])/gi;
const JWT_PATTERN = /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g;
const PRIVATE_KEY_PATTERN =
  /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g;
const DASHED_PROVIDER_TOKEN_PATTERN = /\b(?:sk|pk)-[A-Za-z0-9_-]{8,}\b/g;
const UNDERSCORED_PROVIDER_TOKEN_PATTERN = /\b(?:dp|gsk|sk|pk)_[A-Za-z0-9_-]{8,}\b/g;
const GOOGLE_API_KEY_PATTERN = /\bAIza[0-9A-Za-z_-]{20,}\b/g;
const AWS_ACCESS_KEY_PATTERN =
  /\b(?:A3T[A-Z0-9]|AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASCA)[A-Z0-9]{16}\b/g;
const GITHUB_TOKEN_PATTERN = /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b/g;
const GITHUB_PAT_PATTERN = /\bgithub_pat_[A-Za-z0-9_]{20,}\b/g;
const GITLAB_TOKEN_PATTERN = /\bglpat-[A-Za-z0-9_-]{12,}\b/g;
const LINEAR_TOKEN_PATTERN = /\blin_api_[A-Za-z0-9]{20,}\b/g;
const NPM_TOKEN_PATTERN = /\bnpm_[A-Za-z0-9]{20,}\b/g;
const PYPI_TOKEN_PATTERN = /\bpypi-AgEI[A-Za-z0-9_-]{20,}\b/g;
const SENDGRID_TOKEN_PATTERN = /\bSG\.[A-Za-z0-9_-]{20,}\b/g;
const SLACK_TOKEN_PATTERN = /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/g;
const RECEIVER_SENSITIVE_ID_PATTERN =
  /(?:(?:sk[-_]|ghp_|github_pat_|xox[baprs]-|dp_)[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)/;

export function redactPresentationText(value: string): string {
  return value
    .replace(AUTHORIZATION_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(CREDENTIAL_ASSIGNMENT_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(QUERY_CREDENTIAL_PATTERN, `$1${REDACTED_PRESENTATION_VALUE}`)
    .replace(URL_USERINFO_PATTERN, "$1[Redacted]@")
    .replace(BARE_BEARER_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(JWT_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(PRIVATE_KEY_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(DASHED_PROVIDER_TOKEN_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(UNDERSCORED_PROVIDER_TOKEN_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(GOOGLE_API_KEY_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(AWS_ACCESS_KEY_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(GITHUB_TOKEN_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(GITHUB_PAT_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(GITLAB_TOKEN_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(LINEAR_TOKEN_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(NPM_TOKEN_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(PYPI_TOKEN_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(SENDGRID_TOKEN_PATTERN, REDACTED_PRESENTATION_VALUE)
    .replace(SLACK_TOKEN_PATTERN, REDACTED_PRESENTATION_VALUE);
}

export function isCredentialShapedPresentationId(value: string): boolean {
  return RECEIVER_SENSITIVE_ID_PATTERN.test(value) || redactPresentationText(value) !== value;
}
