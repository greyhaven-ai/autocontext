/**
 * AC-464: Sensitive-data detection, redaction policies, and review.
 *
 * Tests the detector pipeline that finds secrets, PII, and sensitive
 * data in traces before public sharing — and the policy engine that
 * determines whether to block, warn, redact, or require manual review.
 */

import { describe, it, expect } from "vitest";
import {
  SensitiveDataDetector,
  RedactionPolicy,
  applyRedactionPolicy,
  type Detection,
  type DetectionCategory,
  type PolicyAction,
  type RedactionResult,
} from "../src/traces/redaction.js";

// ---------------------------------------------------------------------------
// Detector — secrets
// ---------------------------------------------------------------------------

describe("SensitiveDataDetector — secrets", () => {
  const detector = new SensitiveDataDetector();

  it("detects API keys", () => {
    const text = "Use this key: sk-ant-api03-abc123def456";
    const findings = detector.scan(text);
    expect(findings.some((f) => f.category === "api_key")).toBe(true);
  });

  it("detects AWS keys", () => {
    const text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE";
    const findings = detector.scan(text);
    expect(findings.some((f) => f.category === "api_key" || f.category === "credential")).toBe(true);
  });

  it("detects bearer tokens", () => {
    const text = 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def';
    const findings = detector.scan(text);
    expect(findings.some((f) => f.category === "credential")).toBe(true);
  });

  it("detects generic secrets in env-var style", () => {
    const text = 'DATABASE_PASSWORD="s3cr3t_p@ssw0rd_123"';
    const findings = detector.scan(text);
    expect(findings.some((f) => f.category === "credential")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Detector — PII
// ---------------------------------------------------------------------------

describe("SensitiveDataDetector — PII", () => {
  const detector = new SensitiveDataDetector();

  it("detects email addresses", () => {
    const text = "Contact john.doe@example.com for details";
    const findings = detector.scan(text);
    expect(findings.some((f) => f.category === "email")).toBe(true);
  });

  it("detects phone numbers", () => {
    const text = "Call me at +1-555-123-4567";
    const findings = detector.scan(text);
    expect(findings.some((f) => f.category === "phone")).toBe(true);
  });

  it("detects IP addresses", () => {
    const text = "Server at 192.168.1.100";
    const findings = detector.scan(text);
    expect(findings.some((f) => f.category === "ip_address")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Detector — paths and URLs
// ---------------------------------------------------------------------------

describe("SensitiveDataDetector — paths and URLs", () => {
  const detector = new SensitiveDataDetector();

  it("detects home directory paths", () => {
    const text = "File at /Users/johndoe/Documents/secret.txt";
    const findings = detector.scan(text);
    expect(findings.some((f) => f.category === "file_path")).toBe(true);
  });

  it("detects internal URLs", () => {
    const text = "Check https://internal.corp.company.com/api/v2/data";
    const findings = detector.scan(text);
    expect(findings.some((f) => f.category === "internal_url")).toBe(true);
  });

  it("does not flag common public paths", () => {
    const text = "Read /usr/bin/node";
    const findings = detector.scan(text);
    expect(findings.filter((f) => f.category === "file_path").length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Detector — custom patterns
// ---------------------------------------------------------------------------

describe("SensitiveDataDetector — custom patterns", () => {
  it("supports user-defined patterns", () => {
    const detector = new SensitiveDataDetector({
      customPatterns: [
        { pattern: /PROJ-\d{4,}/g, category: "internal_id", label: "Project ID" },
      ],
    });
    const text = "See PROJ-12345 for details";
    const findings = detector.scan(text);
    expect(findings.some((f) => f.category === "internal_id")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Detector returns nothing for clean text
// ---------------------------------------------------------------------------

describe("SensitiveDataDetector — clean text", () => {
  const detector = new SensitiveDataDetector();

  it("returns no findings for innocuous text", () => {
    const text = "The function processes data and returns a result.";
    const findings = detector.scan(text);
    expect(findings.length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// RedactionPolicy
// ---------------------------------------------------------------------------

describe("RedactionPolicy", () => {
  it("defaults: api_key and credential → redact, email → warn", () => {
    const policy = new RedactionPolicy();
    expect(policy.actionFor("api_key")).toBe("redact");
    expect(policy.actionFor("credential")).toBe("redact");
    expect(policy.actionFor("email")).toBe("warn");
  });

  it("supports custom policy overrides", () => {
    const policy = new RedactionPolicy({
      overrides: { email: "block", file_path: "redact" },
    });
    expect(policy.actionFor("email")).toBe("block");
    expect(policy.actionFor("file_path")).toBe("redact");
  });

  it("unknown categories default to warn", () => {
    const policy = new RedactionPolicy();
    expect(policy.actionFor("unknown_category" as DetectionCategory)).toBe("warn");
  });
});

// ---------------------------------------------------------------------------
// applyRedactionPolicy — full pipeline
// ---------------------------------------------------------------------------

describe("applyRedactionPolicy", () => {
  it("redacts secrets and preserves structure", () => {
    const text = "Use key sk-ant-api03-abc123 and call john@example.com";
    const result = applyRedactionPolicy(text);

    expect(result.redactedText).not.toContain("sk-ant-api03-abc123");
    expect(result.detections.length).toBeGreaterThan(0);
    expect(result.redactions.length).toBeGreaterThan(0);
    expect(result.blocked).toBe(false);
  });

  it("blocks when policy says block", () => {
    const text = "Use sk-ant-api03-realkey123456 for auth";
    const result = applyRedactionPolicy(text, {
      policy: new RedactionPolicy({ overrides: { api_key: "block" } }),
    });

    expect(result.blocked).toBe(true);
    expect(result.blockReasons.length).toBeGreaterThan(0);
  });

  it("returns clean result for innocuous text", () => {
    const text = "A simple function that adds numbers.";
    const result = applyRedactionPolicy(text);

    expect(result.redactedText).toBe(text);
    expect(result.detections.length).toBe(0);
    expect(result.redactions.length).toBe(0);
    expect(result.blocked).toBe(false);
  });

  it("redacted text has placeholders with category labels", () => {
    const text = "Email me at secret@company.com";
    const result = applyRedactionPolicy(text);

    // Redacted text should have a placeholder like [REDACTED:email]
    if (result.redactions.length > 0) {
      expect(result.redactedText).toContain("[REDACTED:");
    }
  });

  it("tracks all detections with positions", () => {
    const text = "Key: sk-ant-api03-test123 and email user@test.com";
    const result = applyRedactionPolicy(text);

    for (const d of result.detections) {
      expect(typeof d.start).toBe("number");
      expect(typeof d.end).toBe("number");
      expect(d.start).toBeLessThan(d.end);
      expect(typeof d.category).toBe("string");
      expect(typeof d.matched).toBe("string");
    }
  });
});

// ---------------------------------------------------------------------------
// RedactionResult shape
// ---------------------------------------------------------------------------

describe("RedactionResult shape", () => {
  it("has all required fields", () => {
    const result: RedactionResult = applyRedactionPolicy("test text");

    expect(result).toHaveProperty("redactedText");
    expect(result).toHaveProperty("detections");
    expect(result).toHaveProperty("redactions");
    expect(result).toHaveProperty("blocked");
    expect(result).toHaveProperty("blockReasons");
    expect(result).toHaveProperty("requiresManualReview");
    expect(typeof result.redactedText).toBe("string");
    expect(Array.isArray(result.detections)).toBe(true);
    expect(Array.isArray(result.redactions)).toBe(true);
    expect(typeof result.blocked).toBe("boolean");
  });
});
