/**
 * Sensitive-data detection and redaction pipeline (AC-464).
 *
 * Detector pipeline for secrets, PII, and sensitive data in traces.
 * Policy engine determines action: block, warn, redact, or require-manual-review.
 *
 * This is the gate for public trace sharing — without it, trace
 * submission is unsafe.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type DetectionCategory =
  | "api_key"
  | "credential"
  | "email"
  | "phone"
  | "ip_address"
  | "file_path"
  | "internal_url"
  | "internal_id"
  | string; // extensible for custom categories

export type PolicyAction = "block" | "warn" | "redact" | "require-manual-approval";

export interface Detection {
  category: DetectionCategory;
  matched: string;
  label: string;
  start: number;
  end: number;
  confidence: number;
}

export interface Redaction {
  category: DetectionCategory;
  original: string;
  replacement: string;
  start: number;
  end: number;
}

export interface RedactionResult {
  redactedText: string;
  detections: Detection[];
  redactions: Redaction[];
  blocked: boolean;
  blockReasons: string[];
  requiresManualReview: boolean;
}

export interface CustomPattern {
  pattern: RegExp;
  category: DetectionCategory;
  label: string;
  confidence?: number;
}

// ---------------------------------------------------------------------------
// Built-in patterns
// ---------------------------------------------------------------------------

interface PatternDef {
  pattern: RegExp;
  category: DetectionCategory;
  label: string;
  confidence: number;
}

const BUILTIN_PATTERNS: PatternDef[] = [
  // API keys
  { pattern: /sk-ant-[a-zA-Z0-9_-]{10,}/g, category: "api_key", label: "Anthropic API key", confidence: 0.95 },
  { pattern: /sk-[a-zA-Z0-9]{20,}/g, category: "api_key", label: "OpenAI API key", confidence: 0.9 },
  { pattern: /AKIA[0-9A-Z]{16}/g, category: "api_key", label: "AWS Access Key", confidence: 0.95 },
  { pattern: /ghp_[a-zA-Z0-9]{36,}/g, category: "api_key", label: "GitHub PAT", confidence: 0.95 },
  { pattern: /glpat-[a-zA-Z0-9_-]{20,}/g, category: "api_key", label: "GitLab PAT", confidence: 0.95 },
  { pattern: /lin_api_[a-zA-Z0-9]{20,}/g, category: "api_key", label: "Linear API key", confidence: 0.95 },

  // Credentials
  { pattern: /Bearer\s+eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+/g, category: "credential", label: "JWT Bearer token", confidence: 0.9 },
  { pattern: /(?:PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)\s*[=:]\s*["']?[^\s"']{8,}["']?/gi, category: "credential", label: "Secret assignment", confidence: 0.8 },

  // PII
  { pattern: /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g, category: "email", label: "Email address", confidence: 0.9 },
  { pattern: /\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}/g, category: "phone", label: "Phone number", confidence: 0.7 },
  { pattern: /\b(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b/g, category: "ip_address", label: "IP address", confidence: 0.8 },

  // Paths (home directories — not /usr/bin or /tmp)
  { pattern: /\/(?:Users|home)\/[a-zA-Z0-9._-]+\/[^\s"'`]+/g, category: "file_path", label: "Home directory path", confidence: 0.8 },
  { pattern: /[A-Z]:\\Users\\[a-zA-Z0-9._-]+\\[^\s"'`]+/g, category: "file_path", label: "Windows user path", confidence: 0.8 },

  // Internal URLs
  { pattern: /https?:\/\/(?:internal|corp|private|staging|dev)\.[a-zA-Z0-9.-]+[^\s)"]*/g, category: "internal_url", label: "Internal URL", confidence: 0.85 },
];

// ---------------------------------------------------------------------------
// Detector
// ---------------------------------------------------------------------------

export class SensitiveDataDetector {
  private patterns: PatternDef[];

  constructor(opts?: { customPatterns?: CustomPattern[] }) {
    this.patterns = [...BUILTIN_PATTERNS];
    if (opts?.customPatterns) {
      for (const cp of opts.customPatterns) {
        this.patterns.push({
          pattern: cp.pattern,
          category: cp.category,
          label: cp.label,
          confidence: cp.confidence ?? 0.8,
        });
      }
    }
  }

  scan(text: string): Detection[] {
    const detections: Detection[] = [];

    for (const def of this.patterns) {
      // Reset regex state for global patterns
      const regex = new RegExp(def.pattern.source, def.pattern.flags);
      let match: RegExpExecArray | null;
      while ((match = regex.exec(text)) !== null) {
        detections.push({
          category: def.category,
          matched: match[0],
          label: def.label,
          start: match.index,
          end: match.index + match[0].length,
          confidence: def.confidence,
        });
      }
    }

    // Deduplicate overlapping detections (keep highest confidence)
    return this.dedup(detections);
  }

  private dedup(detections: Detection[]): Detection[] {
    if (detections.length <= 1) return detections;

    // Sort by start position, then by confidence descending
    detections.sort((a, b) => a.start - b.start || b.confidence - a.confidence);

    const result: Detection[] = [];
    let lastEnd = -1;
    for (const d of detections) {
      if (d.start >= lastEnd) {
        result.push(d);
        lastEnd = d.end;
      }
    }
    return result;
  }
}

// ---------------------------------------------------------------------------
// Policy
// ---------------------------------------------------------------------------

const DEFAULT_POLICY: Record<string, PolicyAction> = {
  api_key: "redact",
  credential: "redact",
  email: "warn",
  phone: "warn",
  ip_address: "warn",
  file_path: "warn",
  internal_url: "warn",
};

export class RedactionPolicy {
  private actions: Record<string, PolicyAction>;

  constructor(opts?: { overrides?: Record<string, PolicyAction> }) {
    this.actions = { ...DEFAULT_POLICY, ...(opts?.overrides ?? {}) };
  }

  actionFor(category: DetectionCategory): PolicyAction {
    return this.actions[category] ?? "warn";
  }
}

// ---------------------------------------------------------------------------
// Apply pipeline
// ---------------------------------------------------------------------------

export function applyRedactionPolicy(
  text: string,
  opts?: { detector?: SensitiveDataDetector; policy?: RedactionPolicy },
): RedactionResult {
  const detector = opts?.detector ?? new SensitiveDataDetector();
  const policy = opts?.policy ?? new RedactionPolicy();

  const detections = detector.scan(text);
  const redactions: Redaction[] = [];
  const blockReasons: string[] = [];
  let requiresManualReview = false;
  let blocked = false;

  // Determine action for each detection
  const toRedact: Detection[] = [];
  for (const d of detections) {
    const action = policy.actionFor(d.category);
    switch (action) {
      case "block":
        blocked = true;
        blockReasons.push(`Blocked: ${d.label} (${d.category}) at position ${d.start}`);
        break;
      case "redact":
        toRedact.push(d);
        break;
      case "require-manual-approval":
        requiresManualReview = true;
        break;
      case "warn":
        // Logged but not acted on
        break;
    }
  }

  // Apply redactions in reverse order to preserve positions
  let redactedText = text;
  const sortedRedactions = [...toRedact].sort((a, b) => b.start - a.start);
  for (const d of sortedRedactions) {
    const replacement = `[REDACTED:${d.category}]`;
    redactedText = redactedText.slice(0, d.start) + replacement + redactedText.slice(d.end);
    redactions.push({
      category: d.category,
      original: d.matched,
      replacement,
      start: d.start,
      end: d.end,
    });
  }

  return {
    redactedText,
    detections,
    redactions: redactions.reverse(), // restore original order
    blocked,
    blockReasons,
    requiresManualReview,
  };
}
