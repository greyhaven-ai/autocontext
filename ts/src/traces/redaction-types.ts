export type DetectionCategory =
  | "api_key"
  | "credential"
  | "email"
  | "phone"
  | "ip_address"
  | "file_path"
  | "internal_url"
  | "internal_id"
  | string;

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

export interface PatternDef {
  pattern: RegExp;
  category: DetectionCategory;
  label: string;
  confidence: number;
}

export interface ScanOptions {
  dedup?: boolean;
}
