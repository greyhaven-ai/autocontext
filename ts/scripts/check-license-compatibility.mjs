#!/usr/bin/env node
/**
 * License-compatibility check per spec section 7.1.
 *
 * Walks package-lock.json, identifies dependencies reachable from the SDK
 * subpath (ajv, ajv-formats, ulid and their transitive deps), and fails
 * CI on any license outside the allowlist.
 *
 * Allowlist: MIT, Apache-2.0, BSD-3-Clause, BSD-2-Clause, ISC, 0BSD,
 * Unlicense, CC0-1.0.
 *
 * Resolution order for each package's license:
 *   1. package-lock.json entry `license` field
 *   2. node_modules/<name>/package.json `license` field (fallback; npm7+
 *      lockfiles sometimes omit license on transitive deps)
 */
import { readFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const PKG_LOCK = join(ROOT, "package-lock.json");
const NODE_MODULES = join(ROOT, "node_modules");

const ALLOWLIST = new Set([
  "MIT",
  "Apache-2.0",
  "BSD-3-Clause",
  "BSD-2-Clause",
  "ISC",
  "0BSD",
  "Unlicense",
  "CC0-1.0",
  "(MIT OR CC0-1.0)",
  "(MIT OR Apache-2.0)",
  "(Apache-2.0 OR MIT)",
  "(BSD-3-Clause OR MIT)",
]);

// Roots: production-traces/sdk direct deps + openai peer dep used by integrations/openai
const SDK_RUNTIME_ROOTS = ["ajv", "ajv-formats", "ulid", "openai"];

if (!existsSync(PKG_LOCK)) {
  console.error("[check-license-compatibility] FAIL - package-lock.json not found");
  process.exit(1);
}

const lock = JSON.parse(readFileSync(PKG_LOCK, "utf-8"));
const packages = lock.packages ?? {};
const byName = new Map();
for (const [path, entry] of Object.entries(packages)) {
  if (path === "") continue;
  const name = entry.name ?? path.split("node_modules/").pop();
  if (!name) continue;
  if (!byName.has(name)) byName.set(name, { path, ...entry });
}

function resolveLicense(name, lockEntry) {
  const fromLock = lockEntry.license
    ?? (Array.isArray(lockEntry.licenses)
      ? lockEntry.licenses.map((l) => (typeof l === "string" ? l : l.type)).join(" OR ")
      : null);
  if (fromLock) return String(fromLock).trim();
  const pkg = join(NODE_MODULES, name, "package.json");
  if (existsSync(pkg)) {
    const data = JSON.parse(readFileSync(pkg, "utf-8"));
    const fromDisk = data.license
      ?? (Array.isArray(data.licenses)
        ? data.licenses.map((l) => (typeof l === "string" ? l : l.type)).join(" OR ")
        : null);
    if (fromDisk) return String(fromDisk).trim();
  }
  return "(missing)";
}

function reachable(roots) {
  const seen = new Set();
  const stack = [...roots];
  while (stack.length > 0) {
    const name = stack.pop();
    if (seen.has(name)) continue;
    const entry = byName.get(name);
    if (!entry) continue;
    seen.add(name);
    const deps = { ...(entry.dependencies ?? {}), ...(entry.peerDependencies ?? {}) };
    for (const depName of Object.keys(deps)) stack.push(depName);
  }
  return seen;
}

const reach = reachable(SDK_RUNTIME_ROOTS);

const offenders = [];
const summary = [];
for (const name of [...reach].sort()) {
  const entry = byName.get(name);
  if (!entry) continue;
  const license = resolveLicense(name, entry);
  summary.push({ name, license, version: entry.version });
  if (!ALLOWLIST.has(license)) {
    offenders.push({ name, license, version: entry.version });
  }
}

if (offenders.length > 0) {
  console.error("[check-license-compatibility] FAIL - non-allowlisted licenses:");
  for (const o of offenders) console.error(`  ${o.name}@${o.version} :: ${o.license}`);
  console.error(`\nAllowlist: ${[...ALLOWLIST].sort().join(", ")}`);
  process.exit(1);
}

console.log(
  `[check-license-compatibility] OK - ${summary.length} packages reachable from SDK subpath, all allowlisted.`,
);
for (const s of summary) {
  console.log(`  ${s.name}@${s.version} :: ${s.license}`);
}
