#!/usr/bin/env node
// Smoke-test fixture: require() the SDK subpath bundle from a CommonJS
// module (which exercises the `"require"` leg of the `"exports"` map).
//
// We reference the built CJS bundle via an explicit relative path rather
// than the package name so the test runs against whatever the current
// repository has built — consistent with how the published package is
// resolved in a consumer's node_modules.
const path = require("node:path");
const bundlePath = path.resolve(
  __dirname,
  "..",
  "..",
  "..",
  "dist",
  "cjs",
  "production-traces",
  "sdk",
  "index.cjs",
);

const sdk = require(bundlePath);

// Assert the full surface is present.
const expected = [
  "buildTrace",
  "writeJsonl",
  "TraceBatch",
  "hashUserId",
  "hashSessionId",
  "loadInstallSalt",
  "initializeInstallSalt",
  "rotateInstallSalt",
  "validateProductionTrace",
  "validateProductionTraceDict",
  "ValidationError",
  "PRODUCTION_TRACE_SCHEMA_VERSION",
];

for (const name of expected) {
  if (!(name in sdk)) {
    console.error(`[cjs-smoke] MISSING export: ${name}`);
    process.exit(1);
  }
}

// Basic behavioral smoke — buildTrace should construct + validate successfully.
const trace = sdk.buildTrace({
  provider: "openai",
  model: "gpt-4o-mini",
  messages: [{ role: "user", content: "hi", timestamp: "2026-04-17T12:00:00.000Z" }],
  timing: {
    startedAt: "2026-04-17T12:00:00.000Z",
    endedAt: "2026-04-17T12:00:01.000Z",
    latencyMs: 1000,
  },
  usage: { tokensIn: 1, tokensOut: 1 },
  env: { environmentTag: "production", appId: "my-app" },
  traceId: "01HZ6X2K7M9A3B4C5D6E7F8G9H",
});

if (trace.schemaVersion !== "1.0") {
  console.error(`[cjs-smoke] unexpected schemaVersion: ${trace.schemaVersion}`);
  process.exit(1);
}

// Hashing smoke — verify a known sha256(salt+value).
const crypto = require("node:crypto");
const salt = "a".repeat(64);
const value = "user-42";
const got = sdk.hashUserId(value, salt);
const expectedHash = crypto.createHash("sha256").update(salt + value).digest("hex");
if (got !== expectedHash) {
  console.error(`[cjs-smoke] hashUserId mismatch: got=${got} expected=${expectedHash}`);
  process.exit(1);
}

console.log("[cjs-smoke] OK");
