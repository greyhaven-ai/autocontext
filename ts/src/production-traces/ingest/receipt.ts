import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { canonicalJsonStringify } from "../../control-plane/contract/canonical-json.js";

/**
 * Companion-file writers for ingestion batches. Both `receipt.json` and
 * `error.json` use canonical JSON (RFC 8785 JCS) so identical input produces
 * byte-identical output — the foundation of P3 idempotence.
 *
 * We reuse `control-plane/contract/canonical-json.ts` here because canonical
 * JSON is a format primitive (not a registry concern); it's referenced
 * explicitly in Foundation A spec §6 as the serialization discipline for
 * receipts and dataset manifests.
 */

export interface ReceiptFields {
  readonly count: number;
  readonly tracesIngested: number;
  readonly duplicatesSkipped: number;
  readonly ingestedAt: string;
  readonly schemaVersion: string;
}

export interface PerLineError {
  readonly lineNo: number;
  readonly attemptedTraceId?: string;
  readonly reasons: readonly string[];
}

export interface ErrorFileFields {
  readonly perLineErrors: readonly PerLineError[];
}

export function writeReceipt(path: string, fields: ReceiptFields): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, canonicalJsonStringify(fields), "utf-8");
}

export function writeErrorFile(path: string, fields: ErrorFileFields): void {
  mkdirSync(dirname(path), { recursive: true });
  // Filter out undefined attemptedTraceId values so canonical JSON accepts
  // the object without complaint.
  const normalized = {
    perLineErrors: fields.perLineErrors.map((e) => ({
      lineNo: e.lineNo,
      reasons: e.reasons,
      ...(e.attemptedTraceId !== undefined ? { attemptedTraceId: e.attemptedTraceId } : {}),
    })),
  };
  writeFileSync(path, canonicalJsonStringify(normalized), "utf-8");
}
