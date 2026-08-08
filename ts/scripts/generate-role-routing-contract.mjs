#!/usr/bin/env node
/**
 * Generates the shared role-routing constants for BOTH packages from
 * docs/role-routing-contract.json, so neither hand-declares them.
 *
 * Usage:
 *   node scripts/generate-role-routing-contract.mjs           # write files
 *   node scripts/generate-role-routing-contract.mjs --check   # diff-only (CI)
 *
 * In --check mode, exits non-zero if either regenerated output differs from its
 * committed file, naming the file, without modifying anything.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const TS_ROOT = resolve(__dirname, "..");
const REPO_ROOT = resolve(TS_ROOT, "..");
const CONTRACT_FILE = join(REPO_ROOT, "docs", "role-routing-contract.json");
const TS_OUT = join(TS_ROOT, "src/providers/role-routing-contract.generated.ts");
const PY_OUT = join(
  REPO_ROOT,
  "autocontext/src/autocontext/agents/role_routing_contract_generated.py",
);

const contract = JSON.parse(readFileSync(CONTRACT_FILE, "utf-8"));

// Deterministic ordering everywhere: regenerating must never produce a spurious diff.
const sortedKeys = (obj) => Object.keys(obj).sort();

// TypeScript array/object literals need ", " (space after comma) and bare (unquoted) keys
// wherever the key is a valid identifier, to match Prettier's default `quoteProps: "as-needed"`
// formatting. JSON.stringify quotes every key and omits the space, which `prettier --write`
// would otherwise rewrite and make the committed file differ from the generator's output.
const isValidTsIdentifier = (key) => /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(key);
const tsKey = (key) => (isValidTsIdentifier(key) ? key : JSON.stringify(key));
const tsArray = (arr) => `[${arr.map((v) => JSON.stringify(v)).join(", ")}]`;

const jsonObj = (obj, indent) =>
  sortedKeys(obj)
    .map((k) => `${indent}${tsKey(k)}: ${JSON.stringify(obj[k])},`)
    .join("\n");
const jsonObjOfArrays = (obj, indent) =>
  sortedKeys(obj)
    .map((k) => `${indent}${tsKey(k)}: ${tsArray([...obj[k]])},`)
    .join("\n");

// Format one contract scalar as a Python literal, and refuse anything else.
//
// JSON.stringify is wrong for Python in two ways. First, JavaScript has no int/float
// distinction, so the contract's `cost_per_1k_tokens.local = 0.0` stringifies to `0` and
// silently lands in Python as an int, changing _COST_TABLE[LOCAL] from 0.0 to 0. Neither
// gate can see it: the parity fixture compares with pytest.approx, and mypy accepts int
// for float via the numeric tower. Second, JSON null/true/false would be written verbatim
// and raise NameError on import, so those are rejected loudly at generation time rather
// than producing a Python file that cannot be imported.
const pyScalar = (value) => {
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isInteger(value) ? `${value}.0` : String(value);
  }
  throw new Error(
    `role-routing contract: cannot emit ${JSON.stringify(value)} as a Python literal. ` +
      `Only strings and finite numbers are supported; null/true/false/objects would produce ` +
      `invalid Python. Fix docs/role-routing-contract.json.`,
  );
};

// Python list/tuple literals need ", " (space after comma) to match ruff's formatter;
// JSON.stringify on an array omits that space, which `ruff format` would otherwise rewrite
// and make the committed file differ from the generator's output.
const pyList = (arr) => `[${arr.map(pyScalar).join(", ")}]`;

const pyObj = (obj, indent) =>
  sortedKeys(obj)
    .map((k) => `${indent}${JSON.stringify(k)}: ${pyScalar(obj[k])},`)
    .join("\n");
const pyObjOfArrays = (obj, indent) =>
  sortedKeys(obj)
    .map((k) => `${indent}${JSON.stringify(k)}: ${pyList([...obj[k]])},`)
    .join("\n");

// Python tuple literal from a JSON array, e.g. ["frontier", "mid_tier"] -> ("frontier", "mid_tier")
// No trailing comma for two or more elements: ruff's formatter collapses `("a", "b",)` to
// `("a", "b")`, so the trailing form would make the committed file differ from ruff's
// canonical output and fail --check forever.
// A ONE-element tuple is the opposite case and needs the trailing comma: `("a")` is a plain
// string, not a tuple, and ruff rewrites it to `"a"` — same permanent --check failure, plus
// a wrong type. ruff preserves `("a",)`.
const pyTuple = (arr) =>
  arr.length === 1 ? `(${pyScalar(arr[0])},)` : `(${arr.map(pyScalar).join(", ")})`;

const tsSource = `/* eslint-disable */
// AUTO-GENERATED from docs/role-routing-contract.json — DO NOT EDIT.
// Regenerate with: node scripts/generate-role-routing-contract.mjs
// CI gate: node scripts/generate-role-routing-contract.mjs --check

export const PROVIDER_CLASSES = ${tsArray([...contract.provider_classes])} as const;

export type ProviderClass = (typeof PROVIDER_CLASSES)[number];

export const ROLE_ROUTING_MODES = ${tsArray([...contract.mode_values])} as const;

export const PROVIDER_CLASS_COST_PER_1K_TOKENS = {
${jsonObj(contract.cost_per_1k_tokens, "  ")}
} as const;

export const DEFAULT_ROLE_ROUTING_TABLE = {
${jsonObjOfArrays(contract.default_routing_table, "  ")}
} as const;

// Kept in the contract's own declaration order, like the per-role preference arrays
// above: order is semantically meaningful for neither, but this makes the two
// consistent, and a committed contract file needs no sort for determinism.
export const LOCAL_ELIGIBLE_ROLES = ${tsArray([...contract.local_eligible_roles])} as const;

// Typed against ProviderClass (not Record<string, string>) so a contract value that
// isn't a declared provider class fails to compile here, instead of surfacing later as
// a mistyped ProviderClass deep inside routing logic.
export const EXPLICIT_PROVIDER_CLASS: Record<string, ProviderClass> = {
${jsonObj(contract.explicit_provider_classes, "  ")}
};
`;

const pySource = `"""AUTO-GENERATED from docs/role-routing-contract.json — DO NOT EDIT.

Regenerate with: node ts/scripts/generate-role-routing-contract.mjs
CI gate:         node ts/scripts/generate-role-routing-contract.mjs --check
"""

from __future__ import annotations

from typing import Final

PROVIDER_CLASSES: Final[tuple[str, ...]] = ${pyTuple([...contract.provider_classes])}

MODE_VALUES: Final[tuple[str, ...]] = ${pyTuple([...contract.mode_values])}

COST_PER_1K_TOKENS: Final[dict[str, float]] = {
${pyObj(contract.cost_per_1k_tokens, "    ")}
}

DEFAULT_ROUTING_TABLE: Final[dict[str, list[str]]] = {
${pyObjOfArrays(contract.default_routing_table, "    ")}
}

LOCAL_ELIGIBLE_ROLES: Final[frozenset[str]] = frozenset(${pyList([...contract.local_eligible_roles])})

EXPLICIT_PROVIDER_CLASSES: Final[dict[str, str]] = {
${pyObj(contract.explicit_provider_classes, "    ")}
}
`;

const targets = [
  [TS_OUT, tsSource],
  [PY_OUT, pySource],
];

if (process.argv.includes("--check")) {
  let drifted = false;
  for (const [file, expected] of targets) {
    const actual = readFileSync(file, "utf-8");
    if (actual !== expected) {
      console.error(`DRIFT: ${file} differs from the contract. Regenerate with:`);
      console.error("  node ts/scripts/generate-role-routing-contract.mjs");
      drifted = true;
    }
  }
  process.exit(drifted ? 1 : 0);
}

for (const [file, source] of targets) {
  writeFileSync(file, source, "utf-8");
  console.log(`wrote ${file}`);
}
