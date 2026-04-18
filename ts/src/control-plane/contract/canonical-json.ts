/**
 * Canonical JSON serialization (RFC 8785 JCS).
 *
 * Produces byte-identical output for logically-equal inputs, regardless of input
 * object key order. Required for PromotionEvent signatures in the control plane.
 *
 * Scope limitations for v1:
 *   - Numbers use JSON.stringify's default formatting. Safe for integers and
 *     finite decimals within IEEE-754 round-trip. NaN and +/-Infinity are rejected.
 *   - Objects with `undefined` values are rejected (not silently dropped), so
 *     signing never accidentally omits content.
 *   - Functions and explicit `undefined` inputs are rejected.
 */

type JsonValue =
  | null
  | boolean
  | number
  | string
  | readonly JsonValue[]
  | { readonly [key: string]: JsonValue };

export function canonicalJsonStringify(value: unknown): string {
  return encode(value, []);
}

function encode(value: unknown, path: readonly (string | number)[]): string {
  if (value === null) return "null";
  if (value === undefined) {
    throw new Error(`canonicalJsonStringify: undefined at ${pathOf(path)} is not representable`);
  }

  const t = typeof value;

  if (t === "boolean") return value ? "true" : "false";

  if (t === "number") {
    const n = value as number;
    if (!Number.isFinite(n)) {
      throw new Error(`canonicalJsonStringify: non-finite number (NaN/Infinity) at ${pathOf(path)}`);
    }
    // JSON.stringify default formatting. Deterministic for safe integers and IEEE-754 round-trip decimals.
    return JSON.stringify(n);
  }

  if (t === "string") return JSON.stringify(value);

  if (t === "function") {
    throw new Error(`canonicalJsonStringify: function at ${pathOf(path)} is not representable`);
  }

  if (Array.isArray(value)) {
    const parts = value.map((item, i) => encode(item, [...path, i]));
    return "[" + parts.join(",") + "]";
  }

  if (t === "object") {
    // Sort by UTF-16 code units — the default behavior of String.prototype.localeCompare
    // is locale-sensitive, so use plain < comparison (which compares code units).
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).sort(codeUnitCompare);
    const parts: string[] = [];
    for (const key of keys) {
      const v = obj[key];
      if (v === undefined) {
        throw new Error(`canonicalJsonStringify: undefined value at ${pathOf([...path, key])}`);
      }
      parts.push(JSON.stringify(key) + ":" + encode(v, [...path, key]));
    }
    return "{" + parts.join(",") + "}";
  }

  throw new Error(`canonicalJsonStringify: unsupported type '${t}' at ${pathOf(path)}`);
}

function codeUnitCompare(a: string, b: string): number {
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

function pathOf(path: readonly (string | number)[]): string {
  return path.length === 0 ? "<root>" : path.map((p) => (typeof p === "number" ? `[${p}]` : `.${p}`)).join("");
}

export type { JsonValue };
