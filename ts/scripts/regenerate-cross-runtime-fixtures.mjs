#!/usr/bin/env node
/**
 * Regenerate committed cross-runtime-emit fixtures.
 *
 * For each fixture directory under tests/_fixtures/cross-runtime-emit,
 * reads inputs.json (camelCase TS-shape BuildTraceInputs), spawns the
 * Python build_trace_canonical.py helper piping those inputs on stdin,
 * captures canonical JSON on stdout, and writes it to
 * python-canonical.json.
 *
 * Never overwrites silently: prints a diff summary for each fixture. Runs
 * synchronously for clarity — this is an operator script, not a hot path.
 *
 * Enterprise-discipline anchor: fixture regeneration is reproducible and
 * deterministic given the Python package state. Any divergence after
 * regeneration is caught by the cross-runtime-fixtures test at PR time.
 */
import { readdirSync, readFileSync, writeFileSync, statSync, existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const FIXTURES = join(ROOT, "tests", "_fixtures", "cross-runtime-emit");
const HELPER = join(ROOT, "tests", "_helpers", "build_trace_canonical.py");
const PY_PKG = resolve(ROOT, "..", "autocontext");

function resolvePython() {
  const override = process.env.AUTOCTX_PARITY_PYTHON;
  if (override && existsSync(override)) return override;
  const venv = join(PY_PKG, ".venv", "bin", "python");
  if (existsSync(venv)) return venv;
  return "python3";
}

const python = resolvePython();
const fixtures = readdirSync(FIXTURES).filter((d) => {
  const p = join(FIXTURES, d);
  return statSync(p).isDirectory();
});

let anyWritten = false;
for (const name of fixtures.sort()) {
  const dir = join(FIXTURES, name);
  const inputsPath = join(dir, "inputs.json");
  const outputPath = join(dir, "python-canonical.json");
  if (!existsSync(inputsPath)) {
    console.warn(`[regen] skip ${name}: missing inputs.json`);
    continue;
  }
  const inputs = readFileSync(inputsPath, "utf-8");
  const r = spawnSync(python, [HELPER], {
    input: inputs,
    encoding: "utf-8",
    env: { ...process.env, PYTHONPATH: join(PY_PKG, "src") },
  });
  if (r.status !== 0) {
    console.error(`[regen] FAIL ${name}:`);
    console.error(r.stderr);
    process.exit(1);
  }
  const next = r.stdout.trim();
  const prev = existsSync(outputPath) ? readFileSync(outputPath, "utf-8").trim() : "";
  if (next !== prev) {
    writeFileSync(outputPath, next + "\n", "utf-8");
    console.log(`[regen] UPDATED ${name} (${prev.length} -> ${next.length} bytes)`);
    anyWritten = true;
  } else {
    console.log(`[regen] OK ${name} (no change)`);
  }
}

if (anyWritten) {
  console.log("\nFixtures regenerated. Review the diffs and commit if intentional.");
} else {
  console.log("\nAll fixtures already match Python output.");
}
