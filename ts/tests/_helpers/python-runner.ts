import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { existsSync } from "node:fs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const HELPER_DIR = __dirname;
const TS_ROOT = resolve(HELPER_DIR, "..", "..");
// Sibling autocontext Python package lives at <repo-root>/autocontext/
const PYTHON_PKG = resolve(TS_ROOT, "..", "autocontext");

export interface PythonRunResult {
  readonly stdout: string;
  readonly stderr: string;
  readonly status: number;
}

/**
 * Resolve the Python interpreter used for cross-runtime parity tests.
 *
 * Preference order:
 *   1. ``AUTOCTX_PARITY_PYTHON`` env var — explicit override
 *   2. Local uv-managed venv at ``autocontext/.venv/bin/python``
 *   3. Plain ``python3`` on PATH
 */
export function resolveParityPython(): string {
  const override = process.env.AUTOCTX_PARITY_PYTHON;
  if (override && existsSync(override)) return override;
  const venv = join(PYTHON_PKG, ".venv", "bin", "python");
  if (existsSync(venv)) return venv;
  return "python3";
}

/**
 * Invoke a Python helper script with the given JSON payload on stdin.
 * Returns the trimmed stdout and the subprocess status.
 *
 * Synchronous (spawnSync) so property-test inner loops stay simple; the
 * overhead is ~500ms cold start + ~100ms per invocation, which is
 * acceptable for the 50/100-run budgets in the cross-runtime property
 * tests.
 */
export function callPythonHelper(scriptName: string, payload: unknown): PythonRunResult {
  const script = join(HELPER_DIR, scriptName);
  if (!existsSync(script)) {
    throw new Error(`python helper missing: ${script}`);
  }
  const result = spawnSync(resolveParityPython(), [script], {
    input: JSON.stringify(payload),
    encoding: "utf-8",
    env: { ...process.env, PYTHONPATH: join(PYTHON_PKG, "src") },
  });
  return {
    stdout: (result.stdout ?? "").trim(),
    stderr: (result.stderr ?? "").trim(),
    status: result.status ?? -1,
  };
}

/**
 * Invoke Python ``build_trace(**snake_case_inputs)`` via the helper at
 * ``build_trace_canonical.py``. Returns the canonical JSON string that
 * Python computed for the resulting dict.
 *
 * Throws if the subprocess fails — cross-runtime parity is the critical
 * safety invariant and silent fallbacks would mask a real divergence.
 */
export function callPythonBuildTrace(inputs: unknown): string {
  const r = callPythonHelper("build_trace_canonical.py", inputs);
  if (r.status !== 0) {
    throw new Error(`python build_trace failed (status ${r.status}):\nstderr:\n${r.stderr}`);
  }
  return r.stdout;
}

export function callPythonHashUserId(userId: string, salt: string): string {
  const r = callPythonHelper("hash_user_id.py", { mode: "user", value: userId, salt });
  if (r.status !== 0) {
    throw new Error(`python hash_user_id failed (status ${r.status}):\nstderr:\n${r.stderr}`);
  }
  return r.stdout;
}

export function callPythonHashSessionId(sessionId: string, salt: string): string {
  const r = callPythonHelper("hash_user_id.py", { mode: "session", value: sessionId, salt });
  if (r.status !== 0) {
    throw new Error(`python hash_session_id failed (status ${r.status}):\nstderr:\n${r.stderr}`);
  }
  return r.stdout;
}

/**
 * Guard for gating parity tests in environments where the Python package
 * is not installed (e.g. a contributor working only on TS). Returns ``true``
 * if the helpers can run end-to-end.
 */
export function isPythonParityAvailable(): boolean {
  try {
    const r = callPythonHelper("hash_user_id.py", { mode: "user", value: "probe", salt: "s" });
    return r.status === 0 && r.stdout.length === 64;
  } catch {
    return false;
  }
}
