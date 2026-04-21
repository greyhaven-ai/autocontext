import type {
  ProductionTrace,
  RedactionMarker,
} from "../contract/types.js";
import type {
  CategoryAction,
  CategoryOverride,
  LoadedRedactionPolicy,
} from "./types.js";
import { hashValue } from "./hash-primitives.js";

/**
 * Apply-at-export redaction (spec §7.3, §7.6).
 *
 * Walks the `redactions[]` markers on a ProductionTrace and rewrites the
 * targeted fields in a DEEP CLONE of the trace. The input is never mutated.
 *
 * For each marker:
 *   1. Look up `categoryOverrides[category ?? reason]`; default action is
 *      `redact`.
 *   2. Apply the action:
 *        - `redact`: replace value with `placeholder` (respecting
 *          `preserveLength` if set — same-length fill with placeholder char 0).
 *        - `hash`:   SHA-256 with install salt (or a per-category hashSalt
 *          override, or empty if both are null) → result is `"sha256:<hex>"`.
 *        - `preserve`: no change.
 *        - `drop`:   remove the field from its parent object.
 *   3. `rawProviderPayload` subtree is stripped entirely if
 *      `exportPolicy.includeRawProviderPayload === false`, regardless of
 *      whether a marker exists — the includes-flag is the authoritative
 *      knob for that subtree.
 *
 * Unresolvable markers (paths that don't exist in the trace) are silently
 * skipped — apply-at-export is a best-effort export boundary, never throws.
 *
 * ## Relationship to `traces/redaction-*`
 *
 * The existing `traces/redaction-application-workflow.ts` operates on flat
 * text with character offsets, not JSON-pointer-based field rewriting. The
 * semantic mismatch is large enough that we re-implement field mutation
 * here. We still share the hashing & placeholder conventions with the
 * existing code (SHA-256, `[redacted]` default).
 *
 * ## Relationship to `redaction/hash-primitives.ts`
 *
 * The `sha256(salt + value)` primitive itself lives in
 * `redaction/hash-primitives.ts` so the customer-facing emit SDK
 * (`sdk/hashing.ts`) can share the algorithm without re-implementing it.
 * Apply-at-export wraps the raw hex digest in the `"sha256:<hex>"` placeholder
 * convention that is specific to the redaction-marker format — the SDK
 * returns the raw hex unchanged.
 */
const RAW_PROVIDER_PAYLOAD_PATH = "/metadata/rawProviderPayload";

type MutableRecord = Record<string, unknown>;

function asMutableRecord(value: unknown): MutableRecord | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  return value as MutableRecord;
}

export function applyRedactions(
  trace: ProductionTrace,
  policy: LoadedRedactionPolicy,
  installSalt: string | null,
): ProductionTrace {
  // Deep clone so the input is never mutated. Traces are pure JSON.
  const clone = structuredClone(trace) as ProductionTrace;

  // Strip rawProviderPayload subtree first if policy excludes it. This is
  // orthogonal to marker-driven redaction — §7.3 step 3 states the subtree
  // is stripped entirely unless `includeRawProviderPayload: true`.
  const includeRawProvider = policy.exportPolicy.includeRawProviderPayload;
  if (!includeRawProvider) {
    const meta = asMutableRecord(clone.metadata);
    if (meta !== null && "rawProviderPayload" in meta) {
      delete meta.rawProviderPayload;
    }
  }

  for (const marker of clone.redactions) {
    // When operator opts into including rawProviderPayload, its markers are
    // ignored — the explicit includes-flag overrides the default redact.
    // Otherwise the subtree has already been stripped; applying a marker to
    // the now-missing field is a harmless no-op but we short-circuit to
    // avoid spurious work.
    if (
      marker.path === RAW_PROVIDER_PAYLOAD_PATH
      || marker.path.startsWith(`${RAW_PROVIDER_PAYLOAD_PATH}/`)
    ) {
      continue;
    }

    const override = resolveOverride(marker, policy);
    const action: CategoryAction = override?.action ?? "redact";
    const placeholder = override?.placeholder ?? policy.exportPolicy.placeholder;
    const hashSalt = override?.hashSalt ?? installSalt ?? "";

    switch (action) {
      case "preserve":
        break;
      case "redact":
        rewriteField(clone, marker.path, (current) =>
          makePlaceholder(current, placeholder, policy.exportPolicy.preserveLength),
        );
        break;
      case "hash":
        rewriteField(clone, marker.path, (current) => `sha256:${hashValue(current, hashSalt)}`);
        break;
      case "drop":
        dropField(clone, marker.path);
        break;
    }
  }

  return clone;
}

function resolveOverride(
  marker: RedactionMarker,
  policy: LoadedRedactionPolicy,
): CategoryOverride | undefined {
  const overrides = policy.exportPolicy.categoryOverrides;
  const categoryKey = marker.category;
  if (categoryKey !== undefined && Object.prototype.hasOwnProperty.call(overrides, categoryKey)) {
    return overrides[categoryKey];
  }
  if (Object.prototype.hasOwnProperty.call(overrides, marker.reason)) {
    return overrides[marker.reason];
  }
  return undefined;
}

function makePlaceholder(current: unknown, placeholder: string, preserveLength: boolean): unknown {
  if (!preserveLength || typeof current !== "string") {
    return placeholder;
  }
  if (placeholder.length === 0) {
    return "".padEnd(current.length, "*");
  }
  // Pad the placeholder (first char repeats) to match the original length.
  const fillChar = placeholder.charAt(0);
  return placeholder.slice(0, current.length).padEnd(current.length, fillChar);
}

// ---- JSON-pointer-based field rewriting ----

/**
 * Rewrite a field at `pointer` within `root` (in-place). Silently no-op if
 * the pointer does not resolve.
 */
function rewriteField(
  root: unknown,
  pointer: string,
  transform: (current: unknown) => unknown,
): void {
  if (pointer === "") return; // Cannot replace root — markers never target root.
  const parts = parsePointer(pointer);
  if (parts === null) return;

  let parent: unknown = root;
  for (let i = 0; i < parts.length - 1; i++) {
    parent = stepInto(parent, parts[i]);
    if (parent === undefined) return;
  }
  const lastKey = parts[parts.length - 1];
  if (Array.isArray(parent)) {
    const idx = toIndex(lastKey);
    if (idx === null || idx >= parent.length) return;
    parent[idx] = transform(parent[idx]);
    return;
  }
  const record = asMutableRecord(parent);
  if (record !== null) {
    if (!Object.prototype.hasOwnProperty.call(record, lastKey)) return;
    record[lastKey] = transform(record[lastKey]);
  }
}

function dropField(root: unknown, pointer: string): void {
  if (pointer === "") return;
  const parts = parsePointer(pointer);
  if (parts === null) return;
  let parent: unknown = root;
  for (let i = 0; i < parts.length - 1; i++) {
    parent = stepInto(parent, parts[i]);
    if (parent === undefined) return;
  }
  const lastKey = parts[parts.length - 1];
  if (Array.isArray(parent)) {
    const idx = toIndex(lastKey);
    if (idx === null || idx >= parent.length) return;
    parent.splice(idx, 1);
    return;
  }
  const record = asMutableRecord(parent);
  if (record !== null && Object.prototype.hasOwnProperty.call(record, lastKey)) {
    delete record[lastKey];
  }
}

function parsePointer(pointer: string): string[] | null {
  if (!pointer.startsWith("/")) return null;
  return pointer.slice(1).split("/").map(unescapeToken);
}

function unescapeToken(t: string): string {
  // Per RFC 6901: decode ~1 before ~0.
  return t.replace(/~1/g, "/").replace(/~0/g, "~");
}

function stepInto(value: unknown, token: string): unknown {
  if (Array.isArray(value)) {
    const idx = toIndex(token);
    if (idx === null || idx >= value.length) return undefined;
    return value[idx];
  }
  const record = asMutableRecord(value);
  if (record === null) return undefined;
  if (!Object.prototype.hasOwnProperty.call(record, token)) return undefined;
  return record[token];
}

function toIndex(token: string): number | null {
  if (!/^(0|[1-9][0-9]*)$/.test(token)) return null;
  return Number(token);
}
