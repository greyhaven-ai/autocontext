#!/usr/bin/env node
/**
 * Side-effect audit per spec section 3.4.
 *
 * Parses every src/ts file, identifies modules whose top level contains
 * expression-statement calls to IMPORTED function names (a strong signal
 * of self-registration into a cross-module registry), and cross-references
 * the set against the sideEffects globs declared in package.json.
 *
 * Failure modes:
 *   Imported-name registrar call NOT matched by any sideEffects glob = FAIL
 *   (bundler tree-shaking would silently drop the side effect).
 *
 * Design note: bare function-name calls to LOCALLY-DEFINED constants
 * (e.g. addFormatsFn(ajv) where both are local) are safe to drop with the
 * containing module. Only bare calls to IMPORTED names signal cross
 * module registry registration, the exact pattern the sideEffects glob
 * exists to protect.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const SRC = join(ROOT, "src");
const PKG = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf-8"));

if (!Array.isArray(PKG.sideEffects)) {
  console.error(
    `[check-side-effects] FAIL - package.json "sideEffects" must be a glob array (got: ${JSON.stringify(
      PKG.sideEffects,
    )})`,
  );
  process.exit(1);
}

const GLOBS = PKG.sideEffects;

function globToRegex(glob) {
  const re = glob
    .split("**")
    .map((p) => p.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, "[^/]*"))
    .join(".*");
  return new RegExp("^" + re + "$");
}
const GLOB_RES = GLOBS.map(globToRegex);

function matchesAnyGlob(relPath) {
  const p = relPath.split(sep).join("/");
  return GLOB_RES.some((r) => r.test(p));
}

function listTsFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) out.push(...listTsFiles(full));
    else if (name.endsWith(".ts") && !name.endsWith(".d.ts")) out.push(full);
  }
  return out;
}

function collectImportedNames(source) {
  const names = new Set();
  const importRe = /^\s*import\s+([^"']+?)\s+from\s+["'][^"']+["']/gm;
  for (const match of source.matchAll(importRe)) {
    const body = match[1].trim();
    if (body.startsWith("{")) {
      const inside = body.slice(1, body.indexOf("}")).trim();
      for (const part of inside.split(",")) {
        const seg = part.trim();
        if (!seg) continue;
        const m = seg.match(/^(?:type\s+)?([A-Za-z_$][A-Za-z0-9_$]*)(?:\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*))?$/);
        if (m) names.add(m[2] ?? m[1]);
      }
    } else if (body.startsWith("*")) {
      const m = body.match(/\*\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)/);
      if (m) names.add(m[1]);
    } else {
      const parts = body.split(",");
      const defaultPart = parts[0].trim();
      if (/^[A-Za-z_$]/.test(defaultPart)) names.add(defaultPart);
      for (const rest of parts.slice(1)) {
        const inner = rest.trim();
        if (inner.startsWith("{")) {
          const inside = inner.slice(1, inner.indexOf("}")).trim();
          for (const seg of inside.split(",")) {
            const s = seg.trim();
            const m = s.match(/^(?:type\s+)?([A-Za-z_$][A-Za-z0-9_$]*)(?:\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*))?$/);
            if (m) names.add(m[2] ?? m[1]);
          }
        }
      }
    }
  }
  return names;
}

const BARE_CALL_RE = /^[ \t]*(?:await\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\s*\(/;

const SAFE_BARE_CALLS = new Set([
  "describe",
  "test",
  "it",
  "beforeEach",
  "afterEach",
  "beforeAll",
  "afterAll",
  "expect",
]);

const IGNORE_KEYWORDS = new Set([
  "if",
  "for",
  "while",
  "switch",
  "throw",
  "new",
  "void",
  "delete",
  "typeof",
  "return",
  "do",
  "yield",
  "catch",
  "with",
]);

/**
 * Strip string literals (double-quoted, single-quoted, and template literals)
 * from a line so braces inside strings don't skew the brace-depth counter.
 * Naive — doesn't handle escaped quotes inside strings comprehensively, but
 * for TS source that's a non-issue (escaped quotes with braces would still
 * parse safely because the brace is lost in the strip).
 */
function stripStringLiterals(line) {
  let out = "";
  let i = 0;
  while (i < line.length) {
    const ch = line[i];
    if (ch === '"' || ch === "'" || ch === "`") {
      const quote = ch;
      i++;
      while (i < line.length) {
        if (line[i] === "\\") {
          i += 2;
          continue;
        }
        if (line[i] === quote) {
          i++;
          break;
        }
        i++;
      }
      out += '""';
      continue;
    }
    out += ch;
    i++;
  }
  return out;
}

/**
 * Strip // line comments and single-line /* ... *\/ block comments.
 * We intentionally do NOT track multi-line block comments — a stray /* in
 * a string (e.g. glob patterns like "**\/x") would be misread as a
 * comment opener. For CI accuracy we just strip string literals first.
 */
function sanitize(line) {
  line = stripStringLiterals(line);
  // strip inline /* ... */
  while (true) {
    const bs = line.indexOf("/*");
    if (bs < 0) break;
    const be = line.indexOf("*/", bs + 2);
    if (be < 0) {
      line = line.slice(0, bs);
      break;
    }
    line = line.slice(0, bs) + line.slice(be + 2);
  }
  const lc = line.indexOf("//");
  if (lc >= 0) line = line.slice(0, lc);
  return line;
}

function hasImportedRegistrarCall(source) {
  const imported = collectImportedNames(source);
  let depth = 0;
  for (const raw of source.split("\n")) {
    const line = sanitize(raw);
    if (line.trim().length === 0) continue;

    if (depth === 0) {
      const trimmed = line.trimStart();
      if (
        !trimmed.startsWith("import")
        && !trimmed.startsWith("export")
        && !trimmed.startsWith("const ")
        && !trimmed.startsWith("let ")
        && !trimmed.startsWith("var ")
        && !trimmed.startsWith("type ")
        && !trimmed.startsWith("interface ")
        && !trimmed.startsWith("class ")
        && !trimmed.startsWith("function ")
        && !trimmed.startsWith("async function ")
        && !trimmed.startsWith("@")
      ) {
        const match = trimmed.match(BARE_CALL_RE);
        if (match) {
          const name = match[1];
          if (
            imported.has(name)
            && !SAFE_BARE_CALLS.has(name)
            && !IGNORE_KEYWORDS.has(name)
          ) {
            return true;
          }
        }
      }
    }
    for (const ch of line) {
      if (ch === "{" || ch === "(" || ch === "[") depth++;
      else if (ch === "}" || ch === ")" || ch === "]") depth = Math.max(0, depth - 1);
    }
  }
  return false;
}

const files = listTsFiles(SRC);

const fails = [];
const registrarFiles = [];
for (const file of files) {
  const rel = relative(ROOT, file);
  const source = readFileSync(file, "utf-8");
  if (hasImportedRegistrarCall(source)) {
    registrarFiles.push(rel);
    const matches = matchesAnyGlob(rel);
    if (!matches) {
      fails.push(
        `UNCOVERED registrar call: ${rel} - top-level call to an IMPORTED function, but file is not in "sideEffects" glob`,
      );
    }
  }
}

if (fails.length > 0) {
  console.error("[check-side-effects] FAIL:");
  for (const msg of fails) console.error("  " + msg);
  console.error(
    `\nEither add the file to package.json "sideEffects" or refactor the top-level call into a function.`,
  );
  process.exit(1);
}

console.log(
  `[check-side-effects] OK - ${files.length} source files audited; ` +
    `${registrarFiles.length} with top-level imported-registrar calls, all covered by globs.`,
);
