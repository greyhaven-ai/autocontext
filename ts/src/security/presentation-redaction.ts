export const REDACTED_PRESENTATION_VALUE = "[Redacted]";

const CREDENTIAL_NAME_PATTERN =
  "(?:[a-z0-9]+[_-])*(?:api[ _-]?key|access[ _-]?key|client[ _-]?secret|refresh[ _-]?token|session[ _-]?(?:key|token)|authorization|auth|bearer|token|secret|password|passphrase|cookie|credential)";
const QUOTED_SECRET_PATTERN = String.raw`(?:"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*')`;
const AUTHORIZATION_PATTERN = new RegExp(
  String.raw`(?:"(?:authorization|proxy-authorization)"|'(?:authorization|proxy-authorization)'|\b(?:authorization|proxy-authorization)\b)\s*[:=]\s*(?:${QUOTED_SECRET_PATTERN}|(?:bearer|basic)\s+[^\s,;]+|digest\s+[^\r\n]+|[^\s,;]+)`,
  "gi",
);
const CREDENTIAL_ASSIGNMENT_PATTERN = new RegExp(
  String.raw`(?<![?&#a-z0-9])(?:"(?:${CREDENTIAL_NAME_PATTERN})"|'(?:${CREDENTIAL_NAME_PATTERN})'|(?:${CREDENTIAL_NAME_PATTERN})\b)\s*[:=]\s*(?:${QUOTED_SECRET_PATTERN}|\[Redacted\]|[^\s,;}\]]+)`,
  "gi",
);
const QUERY_CREDENTIAL_PATTERN =
  /([?&#](?:(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?key|auth|authorization|bearer|client[_-]?secret|refresh[_-]?token|session[_-]?(?:key|token)|token|secret|password|passphrase|signature))=)[^&#\s]+/gi;
const QUERY_ASSIGNMENT_PATTERN = /([?&#])([^=&#\s]{1,256})=([^&#\s]*)/g;
const SENSITIVE_QUERY_KEY_SUFFIXES = [
  "apikey",
  "accesskey",
  "auth",
  "authorization",
  "bearer",
  "clientsecret",
  "refreshtoken",
  "sessionkey",
  "sessiontoken",
  "token",
  "secret",
  "password",
  "passphrase",
  "signature",
] as const;
const URL_USERINFO_PATTERN = /((?:https?|wss?):\/\/)[^/@\s]+@/gi;
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
  const redacted = value
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
  return redactEncodedQueryCredentials(redacted);
}

function redactEncodedQueryCredentials(value: string): string {
  return value.replace(
    QUERY_ASSIGNMENT_PATTERN,
    (assignment, delimiter: string, encodedKey: string) => {
      let decodedKey = encodedKey;
      try {
        decodedKey = decodeURIComponent(encodedKey.replace(/\+/g, "%20"));
      } catch {
        // Fail closed against recognizable undecoded key text below.
      }
      const normalized = decodedKey.toLowerCase().replace(/[^a-z0-9]/g, "");
      return SENSITIVE_QUERY_KEY_SUFFIXES.some((suffix) => normalized.endsWith(suffix))
        ? `${delimiter}${encodedKey}=${REDACTED_PRESENTATION_VALUE}`
        : assignment;
    },
  );
}

export function isCredentialShapedPresentationId(value: string): boolean {
  return RECEIVER_SENSITIVE_ID_PATTERN.test(value) || redactPresentationText(value) !== value;
}
