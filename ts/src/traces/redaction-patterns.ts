import type {
  CustomPattern,
  PatternDef,
  PolicyAction,
} from "./redaction-types.js";

export const BUILTIN_REDACTION_PATTERNS: PatternDef[] = [
  { pattern: /sk-ant-[a-zA-Z0-9_-]{10,}/g, category: "api_key", label: "Anthropic API key", confidence: 0.95 },
  { pattern: /sk-[a-zA-Z0-9]{20,}/g, category: "api_key", label: "OpenAI API key", confidence: 0.9 },
  { pattern: /AKIA[0-9A-Z]{16}/g, category: "api_key", label: "AWS Access Key", confidence: 0.95 },
  { pattern: /ghp_[a-zA-Z0-9]{36,}/g, category: "api_key", label: "GitHub PAT", confidence: 0.95 },
  { pattern: /glpat-[a-zA-Z0-9_-]{20,}/g, category: "api_key", label: "GitLab PAT", confidence: 0.95 },
  { pattern: /lin_api_[a-zA-Z0-9]{20,}/g, category: "api_key", label: "Linear API key", confidence: 0.95 },
  { pattern: /Bearer\s+eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+/g, category: "credential", label: "JWT Bearer token", confidence: 0.9 },
  { pattern: /(?:PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)\s*[=:]\s*["']?[^\s"']{8,}["']?/gi, category: "credential", label: "Secret assignment", confidence: 0.8 },
  { pattern: /xox[bpsa]-[a-zA-Z0-9-]{10,}/g, category: "api_key", label: "Slack token", confidence: 0.95 },
  { pattern: /[sr]k_live_[a-zA-Z0-9]{20,}/g, category: "api_key", label: "Stripe key", confidence: 0.95 },
  { pattern: /pk_live_[a-zA-Z0-9]{20,}/g, category: "api_key", label: "Stripe publishable key", confidence: 0.9 },
  { pattern: /rk_live_[a-zA-Z0-9]{20,}/g, category: "api_key", label: "Stripe restricted key", confidence: 0.95 },
  { pattern: /npm_[a-zA-Z0-9]{20,}/g, category: "api_key", label: "npm token", confidence: 0.95 },
  { pattern: /pypi-AgEI[a-zA-Z0-9_-]{20,}/g, category: "api_key", label: "PyPI token", confidence: 0.95 },
  { pattern: /SG\.[a-zA-Z0-9_-]{20,}/g, category: "api_key", label: "SendGrid key", confidence: 0.9 },
  { pattern: /-----BEGIN[A-Z ]*PRIVATE KEY-----/g, category: "credential", label: "SSH/TLS private key", confidence: 0.99 },
  { pattern: /\b[a-f0-9]{40,}\b/g, category: "credential", label: "Generic hex token", confidence: 0.6 },
  { pattern: /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g, category: "email", label: "Email address", confidence: 0.9 },
  { pattern: /\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}/g, category: "phone", label: "Phone number", confidence: 0.7 },
  {
    pattern: /\b(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b/g,
    category: "ip_address",
    label: "IP address",
    confidence: 0.8,
  },
  { pattern: /\/(?:Users|home)\/[a-zA-Z0-9._-]+\/[^\s"'`]+/g, category: "file_path", label: "Home directory path", confidence: 0.8 },
  { pattern: /[A-Z]:\\Users\\[a-zA-Z0-9._-]+\\[^\s"'`]+/g, category: "file_path", label: "Windows user path", confidence: 0.8 },
  { pattern: /https?:\/\/(?:internal|corp|private|staging|dev)\.[a-zA-Z0-9.-]+[^\s)"]*/g, category: "internal_url", label: "Internal URL", confidence: 0.85 },
];

export const DEFAULT_REDACTION_POLICY: Record<string, PolicyAction> = {
  api_key: "redact",
  credential: "redact",
  email: "warn",
  phone: "warn",
  ip_address: "warn",
  file_path: "warn",
  internal_url: "warn",
};

export function buildDetectorPatterns(
  customPatterns?: CustomPattern[],
): PatternDef[] {
  const patterns = [...BUILTIN_REDACTION_PATTERNS];
  for (const pattern of customPatterns ?? []) {
    patterns.push({
      pattern: pattern.pattern,
      category: pattern.category,
      label: pattern.label,
      confidence: pattern.confidence ?? 0.8,
    });
  }
  return patterns;
}
