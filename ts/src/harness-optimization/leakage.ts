import type { IntegrityMetadata } from "./contract/generated-types.js";

/**
 * Deterministic post-proposal leakage audit (AC-879).
 *
 * TypeScript half of the AC-879 parity pair: the same three-pass audit lives
 * here and in `autocontext/src/autocontext/harness_optimization/leakage.py`,
 * and a shared fixture
 * (`fixtures/harness-optimization/leakage-cases/leakage-cases.json`) proves
 * both languages compute identical statuses and reason counts.
 *
 * Pure functions over declared integrity metadata plus observed access
 * records. No filesystem or network access: the caller supplies the access
 * log. Maps a run to clean | contaminated | unknown so a verified gate can
 * fail closed.
 */

export interface AccessRecord {
  resource: string;
  source_id: string;
  kind: string; // "file" | "trace" | "web" | "split"
}

export interface LeakageAudit {
  status: string; // "clean" | "contaminated" | "unknown"
  reasons: readonly string[];
}

/**
 * Extract the host from a web resource. Mirrors Python's urlparse: a bare host
 * (with or without a path) is parsed under a synthetic `http://` scheme so the
 * host resolves (`docs.example.com/guide` -> `docs.example.com`), and a value
 * that cannot be parsed falls back to the raw resource.
 */
function webHost(resource: string): string {
  try {
    const parsed = new URL(resource.includes("://") ? resource : `http://${resource}`);
    return parsed.hostname || resource;
  } catch {
    return resource;
  }
}

export function auditLeakage(
  metadata: IntegrityMetadata,
  accessRecords: readonly AccessRecord[],
): LeakageAudit {
  const forbidden = new Set(metadata.forbidden_sources);
  const allowlist = new Set(metadata.web_allowlist ?? []);
  const reasons: string[] = [];

  // Pass 1: forbidden source read.
  for (const rec of accessRecords) {
    if (forbidden.has(rec.source_id)) {
      reasons.push(`forbidden source read: ${rec.source_id} (${rec.resource})`);
    }
  }
  // Pass 2: web policy.
  for (const rec of accessRecords) {
    if (rec.kind === "web") {
      const host = webHost(rec.resource);
      if (metadata.web_policy === "blocked") {
        reasons.push(`web access under blocked policy: ${host}`);
      } else if (metadata.web_policy === "allowlist" && !allowlist.has(host)) {
        reasons.push(`web host not in allowlist: ${host}`);
      }
    }
  }

  if (reasons.length > 0) {
    return { status: "contaminated", reasons };
  }

  // Unknown-required fallback: a required source is proven clean if it is a
  // declared allowed source OR appears in the access log.
  const allowed = new Set(metadata.allowed_sources);
  const covered = new Set(accessRecords.map((rec) => rec.source_id));
  const unknown = metadata.required_sources.filter((s) => !covered.has(s) && !allowed.has(s));
  if (unknown.length > 0) {
    return {
      status: "unknown",
      reasons: unknown.map((s) => `required source unproven: ${s}`),
    };
  }
  return { status: "clean", reasons: [] };
}

/**
 * Render a short multi-line report of the metadata policy and the computed
 * audit. Lists mode, allowed_sources, forbidden_sources, required_sources,
 * web_policy, web_allowlist, the computed status, and each reason prefixed with
 * `- `.
 */
export function renderLeakageReport(metadata: IntegrityMetadata, audit: LeakageAudit): string {
  const lines = [
    "autocontext leakage audit",
    `mode: ${metadata.mode}`,
    `allowed_sources: ${metadata.allowed_sources.join(", ")}`,
    `forbidden_sources: ${metadata.forbidden_sources.join(", ")}`,
    `required_sources: ${metadata.required_sources.join(", ")}`,
    `web_policy: ${metadata.web_policy}`,
    `web_allowlist: ${(metadata.web_allowlist ?? []).join(", ")}`,
    `status: ${audit.status}`,
  ];
  for (const reason of audit.reasons) {
    lines.push(`- ${reason}`);
  }
  return lines.join("\n");
}
