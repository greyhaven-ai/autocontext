#!/usr/bin/env node
/**
 * No-postinstall-scripts check per spec section 7.2.
 *
 * Two layers:
 *
 *   1. STRICT (self + transitive): preinstall, install, postinstall —
 *      these run on a plain `npm install` from a tarball (what customers
 *      do). Fails CI if any package declares them.
 *
 *   2. SELF-ONLY (self package): prepublish, prepare — these run at
 *      publish time (and, for npm install-from-git, at install time).
 *      For npm-registry installs they don't fire on customer machines,
 *      so transitive-dep hooks are tolerated. The autoctx package itself
 *      must declare none of them so that publishing is deterministic.
 *
 * Enterprise environments commonly use `npm install --ignore-scripts`;
 * both layers combined let us ship the SDK with a clear "no scripts
 * execute on customer install" guarantee.
 */
import { readFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const PKG_LOCK = join(ROOT, "package-lock.json");
const NODE_MODULES = join(ROOT, "node_modules");
const SELF_PKG = join(ROOT, "package.json");

const STRICT_HOOKS = ["preinstall", "install", "postinstall"];
const SELF_ONLY_HOOKS = ["prepublish", "prepare"];

// Roots: production-traces/sdk direct deps + openai peer dep used by integrations/openai
const SDK_RUNTIME_ROOTS = ["ajv", "ajv-formats", "ulid", "openai"];

function loadPkg(path) {
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }
}

const self = loadPkg(SELF_PKG) ?? {};
const selfScripts = self.scripts ?? {};

const fails = [];

for (const hook of [...STRICT_HOOKS, ...SELF_ONLY_HOOKS]) {
  if (hook in selfScripts) {
    fails.push(`autoctx (self) declares "${hook}": ${selfScripts[hook]}`);
  }
}

if (!existsSync(PKG_LOCK)) {
  console.error("[check-no-postinstall-scripts] FAIL - package-lock.json not found");
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

for (const name of [...reach].sort()) {
  const pkg = loadPkg(join(NODE_MODULES, name, "package.json"));
  if (!pkg) continue;
  const scripts = pkg.scripts ?? {};
  for (const hook of STRICT_HOOKS) {
    if (hook in scripts) {
      fails.push(`${name}@${pkg.version} declares "${hook}": ${scripts[hook]}`);
    }
  }
}

if (fails.length > 0) {
  console.error("[check-no-postinstall-scripts] FAIL:");
  for (const msg of fails) console.error("  " + msg);
  console.error(
    `\nStrict hooks (self + transitive): ${STRICT_HOOKS.join(", ")}\n` +
      `Self-only hooks: ${SELF_ONLY_HOOKS.join(", ")}\n` +
      `Enterprise installers commonly run with --ignore-scripts; these hooks would be silently skipped there.`,
  );
  process.exit(1);
}

console.log(
  `[check-no-postinstall-scripts] OK - autoctx declares no install-time hooks; ${reach.size} transitive deps clean.`,
);
