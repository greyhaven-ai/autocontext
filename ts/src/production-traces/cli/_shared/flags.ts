// Minimal flag parser shared by production-traces CLI commands.
//
// Matches the shape used inside `control-plane/cli/candidate.ts` but extracted
// here because every command in this namespace parses a small, heterogeneous
// flag set and copy-pasting the parser per file was the biggest source of
// drift in the Foundation B CLI (noted in the Layer 7-8 post-mortem).
//
// Supported spec shapes:
//   { type: "string" }                    → single string value
//   { type: "string", default: "pretty" } → default-applied if absent
//   { type: "string", required: true }    → error if absent
//   { type: "string-array" }              → flag can repeat; values collected
//   { type: "boolean" }                   → flag presence → true; no value consumed
//
// Positional arguments (tokens not starting with `--`) are ignored here; the
// caller is expected to slice them off before passing argv to `parseFlags`.

export type FlagType = "string" | "string-array" | "boolean";

export interface FlagSpec {
  readonly type: FlagType;
  readonly required?: boolean;
  readonly default?: string;
}

export type ParsedFlags = Record<string, string | readonly string[] | boolean | undefined>;

export type FlagsResult =
  | { readonly value: ParsedFlags }
  | { readonly error: string };

export function parseFlags(
  args: readonly string[],
  spec: Readonly<Record<string, FlagSpec>>,
): FlagsResult {
  const parsed: Record<string, string | string[] | boolean | undefined> = {};
  for (let i = 0; i < args.length; i++) {
    const a = args[i]!;
    if (!a.startsWith("--")) continue;
    const name = a.slice(2);
    if (!(name in spec)) {
      return { error: `Unknown flag: --${name}` };
    }
    const s = spec[name]!;
    if (s.type === "boolean") {
      parsed[name] = true;
      continue;
    }
    const next = args[i + 1];
    if (next === undefined || next.startsWith("--")) {
      return { error: `Flag --${name} requires a value` };
    }
    if (s.type === "string-array") {
      const prior = parsed[name];
      const arr = Array.isArray(prior) ? prior : [];
      arr.push(next);
      parsed[name] = arr;
    } else {
      parsed[name] = next;
    }
    i += 1;
  }
  for (const [key, s] of Object.entries(spec)) {
    const v = parsed[key];
    if (v === undefined) {
      if (s.default !== undefined) {
        parsed[key] = s.default;
      } else if (s.required === true) {
        return { error: `Missing required flag: --${key}` };
      }
    }
  }
  return { value: parsed as ParsedFlags };
}

/**
 * Extract a string value from a ParsedFlags map. Returns `undefined` if unset,
 * throws if present-but-wrong-type (should be unreachable because `parseFlags`
 * enforces per-spec shape — guard serves as a runtime type check for the
 * TS-narrowing at the call site).
 */
export function stringFlag(
  flags: ParsedFlags,
  name: string,
): string | undefined {
  const v = flags[name];
  if (v === undefined) return undefined;
  if (typeof v !== "string") {
    throw new Error(`stringFlag: expected --${name} to be string, got ${typeof v}`);
  }
  return v;
}

export function booleanFlag(flags: ParsedFlags, name: string): boolean {
  const v = flags[name];
  return v === true;
}

export function stringArrayFlag(flags: ParsedFlags, name: string): readonly string[] {
  const v = flags[name];
  if (v === undefined) return [];
  if (!Array.isArray(v)) {
    throw new Error(`stringArrayFlag: expected --${name} to be array, got ${typeof v}`);
  }
  return v;
}
