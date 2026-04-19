/**
 * TypeScript shape for the redaction policy config, matching
 * `json-schemas/redaction-policy.schema.json`. We could use the generated
 * `RedactionPolicy` interface, but hand-writing the type lets us keep `as const`
 * friendliness and narrow union literal types for callers.
 *
 * See spec §7.1 for the on-disk shape.
 */
import type { RedactionReason } from "../contract/types.js";

export type RedactionMode = "on-export" | "on-ingest";

export type RawProviderPayloadBehavior = "blanket-mark";

export type CategoryAction = "redact" | "hash" | "preserve" | "drop";

export type CategoryOverride = {
  readonly action: CategoryAction;
  readonly placeholder?: string;
  readonly hashSalt?: string;
};

export type ExportPolicy = {
  readonly placeholder: string;
  readonly preserveLength: boolean;
  readonly includeRawProviderPayload: boolean;
  readonly includeMetadata: boolean;
  readonly categoryOverrides: Readonly<Record<string, CategoryOverride>>;
};

export type CustomPolicyPattern = {
  readonly name: string;
  readonly regex: string;
  readonly category: string;
  readonly reason: RedactionReason;
};

export type LoadedRedactionPolicy = {
  readonly schemaVersion: "1.0";
  readonly mode: RedactionMode;
  readonly autoDetect: {
    readonly enabled: boolean;
    readonly categories: readonly string[];
  };
  readonly customPatterns: readonly CustomPolicyPattern[];
  readonly rawProviderPayload: {
    readonly behavior: RawProviderPayloadBehavior;
  };
  readonly exportPolicy: ExportPolicy;
};
