#!/usr/bin/env node
/**
 * No-customer-side-telemetry check per spec section 7.3.
 *
 * Greps the SDK source plus transitive-dep sources for patterns that
 * would indicate the SDK or its deps phone home:
 *
 *   - fetch( to a non-relative URL literal
 *   - http.request / https.request to non-localhost hosts
 *   - imports of known telemetry SDKs (@sentry/*, posthog-*,
 *     mixpanel-*, segment/*, amplitude-*, @datadog/*, @honeycombio/*)
 *
 * This is an intentionally conservative check — false-positives are
 * acceptable; the SDK is a pure filesystem emitter. Any network code
 * inside the SDK's reach should trigger a PR review, not silent
 * shipping.
 */
import { readdirSync, readFileSync, statSync, existsSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const NODE_MODULES = join(ROOT, "node_modules");
const PKG_LOCK = join(ROOT, "package-lock.json");

// Source directories for all shipped subpaths
const SUBPATH_SRC_DIRS = [
  join(ROOT, "src", "production-traces", "sdk"),
  join(ROOT, "src", "integrations", "openai"),
  join(ROOT, "src", "control-plane", "instrument", "detectors", "openai-python"),
  join(ROOT, "src", "control-plane", "instrument", "detectors", "openai-ts"),
];

// Roots: production-traces/sdk direct deps + openai peer dep used by integrations/openai
const SDK_RUNTIME_ROOTS = ["ajv", "ajv-formats", "ulid", "openai"];

const TELEMETRY_IMPORT_RES = [
  /from\s+["']@sentry\//,
  /from\s+["']posthog[-\w/]*["']/,
  /from\s+["']mixpanel[-\w/]*["']/,
  /from\s+["']@segment\//,
  /from\s+["']amplitude[-\w/]*["']/,
  /from\s+["']@datadog\//,
  /from\s+["']@honeycombio\//,
  /from\s+["']rudder-sdk/,
  /from\s+["']@vercel\/analytics/,
  /require\(["']@sentry\//,
  /require\(["']posthog[-\w/]*["']/,
  /require\(["']mixpanel[-\w/]*["']/,
];

// External fetch() to a hardcoded non-relative URL. Accept local/relative
// URLs (customer-provided, localhost, file:// etc.).
const FETCH_EXTERNAL_RE = /fetch\s*\(\s*["'](https?:\/\/(?!(?:localhost|127\.0\.0\.1|0\.0\.0\.0))[^"']+)["']/;
const HTTP_REQUEST_EXTERNAL_RE = /(?:http|https)\.request\s*\(\s*["'](https?:\/\/(?!(?:localhost|127\.0\.0\.1))[^"']+)["']/;

function listSourceFiles(dir, exts) {
  if (!existsSync(dir)) return [];
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    const st = statSync(full, { throwIfNoEntry: false });
    if (!st) continue;
    if (st.isDirectory()) {
      // Skip `node_modules/.bin` and other non-source dirs.
      if (name === ".bin" || name === ".cache") continue;
      out.push(...listSourceFiles(full, exts));
    } else if (exts.some((e) => name.endsWith(e))) out.push(full);
  }
  return out;
}

const lock = existsSync(PKG_LOCK) ? JSON.parse(readFileSync(PKG_LOCK, "utf-8")) : { packages: {} };
const byName = new Map();
for (const [path, entry] of Object.entries(lock.packages ?? {})) {
  if (path === "") continue;
  const name = entry.name ?? path.split("node_modules/").pop();
  if (!name) continue;
  if (!byName.has(name)) byName.set(name, entry);
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

const sdkReach = reachable(SDK_RUNTIME_ROOTS);
const filesToScan = [
  ...SUBPATH_SRC_DIRS.flatMap((dir) => listSourceFiles(dir, [".ts"])),
  ...[...sdkReach].flatMap((n) =>
    listSourceFiles(join(NODE_MODULES, n), [".js", ".mjs", ".cjs"]),
  ),
];

const offenses = [];
for (const file of filesToScan) {
  const rel = relative(ROOT, file);
  let body;
  try {
    body = readFileSync(file, "utf-8");
  } catch {
    continue;
  }
  if (body.length > 2_000_000) continue; // skip huge generated files
  for (const re of TELEMETRY_IMPORT_RES) {
    if (re.test(body)) {
      offenses.push({ file: rel, kind: "telemetry-sdk-import", detail: re.source });
      break;
    }
  }
  const fetchMatch = body.match(FETCH_EXTERNAL_RE);
  if (fetchMatch) {
    offenses.push({ file: rel, kind: "external-fetch", detail: fetchMatch[1] });
  }
  const reqMatch = body.match(HTTP_REQUEST_EXTERNAL_RE);
  if (reqMatch) {
    offenses.push({ file: rel, kind: "external-http-request", detail: reqMatch[1] });
  }
}

if (offenses.length > 0) {
  console.error("[check-no-telemetry] FAIL:");
  for (const o of offenses) console.error(`  ${o.kind} :: ${o.file} :: ${o.detail}`);
  console.error(
    `\nSDK README states: "Zero telemetry. Traces go where you put them." Any of the above patterns may contradict that promise and must be reviewed before shipping.`,
  );
  process.exit(1);
}

console.log(
  `[check-no-telemetry] OK - scanned ${filesToScan.length} files (SDK source + ${sdkReach.size} transitive deps); no telemetry patterns detected.`,
);
