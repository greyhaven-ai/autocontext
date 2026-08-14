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
  "autocontext/src/autocontext/config/role_routing_contract_generated.py",
);

const contract = JSON.parse(readFileSync(CONTRACT_FILE, "utf-8"));

// Contract-level invariants. These are cheap here and expensive later: a transport
// present in one map and absent from the other resolves to a silent default deep
// inside a routing decision, where it looks like a routing bug rather than a typo
// in a JSON file.
const sameKeys = (a, b) => {
  const left = Object.keys(a).sort();
  const right = Object.keys(b).sort();
  return left.length === right.length && left.every((key, i) => key === right[i]);
};
if (!sameKeys(contract.provider_hosting, contract.explicit_provider_classes)) {
  throw new Error(
    "role-routing contract: provider_hosting and explicit_provider_classes must cover " +
      "exactly the same transports. Every transport has both a capability and a hosting.",
  );
}
const rankedClasses = Object.keys(contract.capability_rank).sort();
const inferredClasses = [...new Set(Object.values(contract.explicit_provider_classes))].sort();
const unranked = inferredClasses.filter(
  (name) => !rankedClasses.includes(name) && name !== "local" && name !== "code_policy",
);
if (unranked.length > 0) {
  throw new Error(
    `role-routing contract: capability classes ${JSON.stringify(unranked)} are inferred for ` +
      "some transport but have no capability_rank, so they cannot be compared against a " +
      "role's requirement.",
  );
}
if (!(contract.local_artifact_capability in contract.capability_rank)) {
  throw new Error(
    `role-routing contract: local_artifact_capability ` +
      `${JSON.stringify(contract.local_artifact_capability)} has no capability_rank entry.`,
  );
}

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
// Providers that serve real tiers map class -> model id, so this one nests.
const jsonObjOfObjs = (obj, indent) =>
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
const pyObjOfObjs = (obj, indent) =>
  sortedKeys(obj)
    .map(
      (k) =>
        `${indent}${JSON.stringify(k)}: {\n${sortedKeys(obj[k])
          .map((t) => `${indent}    ${JSON.stringify(t)}: ${pyScalar(obj[k][t])},`)
          .join("\n")}\n${indent}},`,
    )
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

import type { StringSettingKey } from "./role-routing.js";

export const PROVIDER_CLASSES = ${tsArray([...contract.provider_classes])} as const;

export type ProviderClass = (typeof PROVIDER_CLASSES)[number];

export const ROLE_ROUTING_MODES = ${tsArray([...contract.mode_values])} as const;

export const PROVIDER_CLASS_COST_PER_1K_TOKENS = {
${jsonObj(contract.cost_per_1k_tokens, "  ")}
} as const;

export const DEFAULT_ROLE_ROUTING_TABLE = {
${jsonObjOfArrays(contract.default_routing_table, "  ")}
} as const;

// Capability ordering, so a role's requirement can be compared against what an
// endpoint declares. Only the API-backed classes are ranked: "local" names an
// artifact slot rather than a capability, and "code_policy" is not model-backed.
export const CAPABILITY_RANK: Record<string, number> = {
${jsonObj(contract.capability_rank, "  ")}
};

// What a distilled local artifact is treated as being capable of. Declared rather
// than assumed, because it is the value that decides which roles an artifact may
// serve once eligibility is derived instead of hardcoded.
export const LOCAL_ARTIFACT_CAPABILITY = ${JSON.stringify(contract.local_artifact_capability)};

// Conservative hosting fallback for endpoints without an explicit declaration.
// Endpoint settings override this transport-based value because generic transports
// such as openai-compatible may be local and vllm may be hosted remotely.
export const PROVIDER_HOSTING: Record<string, string> = {
${jsonObj(contract.provider_hosting, "  ")}
};

// Model id to send when the user has configured none and the provider is not one
// whose defaults are preserved. Declared once here because AC-912 shipped this
// table in Python only, and the TypeScript engine went on sending Claude ids to
// every self-hosted endpoint with nothing to catch it.
export const PROVIDER_DEFAULT_MODEL: Record<string, string> = {
${jsonObj(contract.provider_default_model, "  ")}
};

// Per-tier defaults for providers that actually SERVE tiers (AC-935). A provider
// absent here keeps the single PROVIDER_DEFAULT_MODEL entry, which is correct for
// an endpoint serving one model rather than an omission.
export const PROVIDER_TIER_MODELS: Record<string, Record<string, string>> = {
${jsonObjOfObjs(contract.provider_tier_models ?? {}, "  ")}
};

// Providers whose shipped model defaults must never be rewritten.
export const MODEL_DEFAULT_PRESERVED_PROVIDERS = ${tsArray([...contract.model_default_preserved_providers])} as const;

// Typed against ProviderClass (not Record<string, string>) so a contract value that
// isn't a declared provider class fails to compile here, instead of surfacing later as
// a mistyped ProviderClass deep inside routing logic.
export const EXPLICIT_PROVIDER_CLASS: Record<string, ProviderClass> = {
${jsonObj(contract.explicit_provider_classes, "  ")}
};

// Python settings key -> the RoleRoutingSettings field holding the same value.
// Typed against \`StringSettingKey\` so a contract entry naming a field TypeScript
// does not have fails to compile here, rather than silently dropping that setting
// when a test replays a shared fixture.
export const SETTINGS_KEY_MAP: Record<string, StringSettingKey> = {
${jsonObj(contract.settings_keys, "  ")}
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

CAPABILITY_RANK: Final[dict[str, int]] = {
${sortedKeys(contract.capability_rank)
  .map((k) => `    ${JSON.stringify(k)}: ${contract.capability_rank[k]},`)
  .join("\n")}
}

LOCAL_ARTIFACT_CAPABILITY: Final[str] = ${JSON.stringify(contract.local_artifact_capability)}

PROVIDER_HOSTING: Final[dict[str, str]] = {
${pyObj(contract.provider_hosting, "    ")}
}

PROVIDER_DEFAULT_MODEL: Final[dict[str, str]] = {
${pyObj(contract.provider_default_model, "    ")}
}

PROVIDER_TIER_MODELS: Final[dict[str, dict[str, str]]] = {
${pyObjOfObjs(contract.provider_tier_models ?? {}, "    ")}
}

MODEL_DEFAULT_PRESERVED_PROVIDERS: Final[frozenset[str]] = frozenset(${pyList([...contract.model_default_preserved_providers])})

EXPLICIT_PROVIDER_CLASSES: Final[dict[str, str]] = {
${pyObj(contract.explicit_provider_classes, "    ")}
}

# Python settings key -> the TypeScript field holding the same value. Python reads
# the keys; TypeScript reads the values. Declared once so neither package can add a
# routing-relevant setting the other never learns about.
SETTINGS_KEYS: Final[dict[str, str]] = {
${pyObj(contract.settings_keys, "    ")}
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
