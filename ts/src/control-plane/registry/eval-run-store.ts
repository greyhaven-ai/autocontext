import {
  mkdirSync,
  writeFileSync,
  readFileSync,
  readdirSync,
  existsSync,
  statSync,
} from "node:fs";
import { join } from "node:path";
import type { EvalRun } from "../contract/types.js";
import { validateEvalRun } from "../contract/validators.js";
import { canonicalJsonStringify } from "../contract/canonical-json.js";

const EVAL_RUNS_DIR = "eval-runs";

/**
 * Persist an EvalRun under `<artifactDir>/eval-runs/<runId>.json`.
 *
 * Refuses if the EvalRun fails schema validation.
 */
export function saveEvalRun(artifactDir: string, run: EvalRun): void {
  const v = validateEvalRun(run);
  if (!v.valid) {
    throw new Error(`saveEvalRun: invalid EvalRun: ${v.errors.join("; ")}`);
  }
  const dir = join(artifactDir, EVAL_RUNS_DIR);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, `${run.runId}.json`), canonicalJsonStringify(run), "utf-8");
}

/**
 * Read an EvalRun by runId. Throws if the file is missing or malformed.
 */
export function loadEvalRun(artifactDir: string, runId: string): EvalRun {
  const path = join(artifactDir, EVAL_RUNS_DIR, `${runId}.json`);
  if (!existsSync(path)) {
    throw new Error(`loadEvalRun: runId ${runId} not found at ${path}`);
  }
  const raw = readFileSync(path, "utf-8");
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new Error(`loadEvalRun: ${path} is not valid JSON: ${(err as Error).message}`);
  }
  const v = validateEvalRun(parsed);
  if (!v.valid) {
    throw new Error(`loadEvalRun: stored EvalRun failed validation: ${v.errors.join("; ")}`);
  }
  return parsed as EvalRun;
}

/**
 * List every runId stored under `<artifactDir>/eval-runs/`.
 */
export function listEvalRunIds(artifactDir: string): string[] {
  const dir = join(artifactDir, EVAL_RUNS_DIR);
  if (!existsSync(dir)) return [];
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (!entry.endsWith(".json")) continue;
    const full = join(dir, entry);
    try {
      if (statSync(full).isFile()) {
        out.push(entry.slice(0, -".json".length));
      }
    } catch {
      // ignore
    }
  }
  return out;
}
