// Python subprocess orchestration helper for the Layer 9 integration flows
// that exercise the cross-runtime boundary (flow 1, flow 6).
//
// DRY: wraps `child_process.spawnSync("uv", ...)` so the flow tests don't
// re-implement subprocess plumbing. Injects `AUTOCONTEXT_REGISTRY_PATH` so
// Python's `write_jsonl` drops batches into the TS-side fixture registry.
//
// DDD: `runPythonEmit(opts)` mirrors the customer-side emit verb. Output is
// a `{ status, stdout, stderr, batchPath }` tuple — the test asserts on
// whichever fields it needs.
//
// Skip-gracefully: `isUvAvailable()` lets flow tests call `describe.skip` in
// CI environments without `uv` on PATH. Matches the pattern established by
// `cross-runtime/python-emit-roundtrip.test.ts`.

import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const TS_TESTS_ROOT = resolve(__dirname, "..", "..", "..", "..", "..");
const WORKTREE_ROOT = resolve(TS_TESTS_ROOT, "..");
const PYTHON_CWD = resolve(WORKTREE_ROOT, "autocontext");

export function isUvAvailable(): boolean {
  const r = spawnSync("uv", ["--version"], { encoding: "utf-8" });
  return r.status === 0;
}

export interface RunPythonScriptResult {
  readonly status: number;
  readonly stdout: string;
  readonly stderr: string;
}

/**
 * Run `uv run python -c <script>` in the `autocontext/` Python package dir.
 * Environment: `AUTOCONTEXT_REGISTRY_PATH` is set to `registryPath` so any
 * call to `write_jsonl(trace)` (without an explicit cwd) lands in the
 * caller-supplied tmpdir.
 */
export function runPythonScript(
  script: string,
  opts: { readonly registryPath: string; readonly stdin?: string },
): RunPythonScriptResult {
  const result = spawnSync("uv", ["run", "python", "-c", script], {
    cwd: PYTHON_CWD,
    encoding: "utf-8",
    env: {
      ...process.env,
      AUTOCONTEXT_REGISTRY_PATH: opts.registryPath,
    },
    ...(opts.stdin !== undefined ? { input: opts.stdin } : {}),
  });
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
  };
}

export interface RunPythonEmitOptions {
  /** Absolute path of a tmpdir; the script writes under <registryPath>/.autocontext/. */
  readonly registryPath: string;
  /** How many traces to emit (looped `build_trace` + single `write_jsonl`). */
  readonly count: number;
  /** Optional taskType override; applied to every emitted trace's `env.taskType`. */
  readonly taskType?: string;
  /** Optional ULID prefix for traceIds; default uses a stable deterministic series. */
  readonly traceIdPrefix?: string;
  /** Optional explicit batchId so tests can assert on the output filename. */
  readonly batchId?: string;
  /** Optional startedAt anchor; defaults to 2026-04-17T12:00:00.000Z. */
  readonly startedAt?: string;
}

export interface RunPythonEmitResult {
  readonly status: number;
  readonly stdout: string;
  readonly stderr: string;
  /** Absolute path of the written JSONL batch file (stdout's last line). */
  readonly batchPath: string;
}

/**
 * Invoke the Python SDK to `build_trace` N times and `write_jsonl` them as a
 * single batch under `<registryPath>/.autocontext/production-traces/incoming/`.
 *
 * The script is deliberately compact (single-shot subprocess) — flows that
 * need more control can compose `runPythonScript` directly.
 */
export function runPythonEmit(opts: RunPythonEmitOptions): RunPythonEmitResult {
  const count = opts.count;
  if (count < 1) {
    throw new Error(`runPythonEmit: count must be >= 1 (got ${count})`);
  }
  const anchor = opts.startedAt ?? "2026-04-17T12:00:00.000Z";
  const batchId = opts.batchId ?? "";
  const taskType = opts.taskType ?? "";
  const idPrefix = opts.traceIdPrefix ?? "01K000000000000000000A";

  const script = [
    "import json, sys",
    "from datetime import datetime, timedelta, timezone",
    "from autocontext.production_traces import build_trace, write_jsonl",
    "",
    `anchor = datetime.fromisoformat(${JSON.stringify(anchor.replace("Z", "+00:00"))})`,
    `count = ${count}`,
    `task_type = ${JSON.stringify(taskType)}`,
    `id_prefix = ${JSON.stringify(idPrefix)}`,
    `batch_id = ${JSON.stringify(batchId)}`,
    "",
    "traces = []",
    "for i in range(count):",
    '    suffix = format(i, "04X")',
    '    trace_id = (id_prefix + suffix)[:26]',
    "    started = anchor + timedelta(seconds=i)",
    "    ended = started + timedelta(seconds=1)",
    "    env = {",
    '        "environmentTag": "production",',
    '        "appId": "my-app",',
    "    }",
    "    if task_type:",
    '        env["taskType"] = task_type',
    "    trace = build_trace(",
    '        provider="anthropic",',
    '        model="claude-sonnet-4-20250514",',
    "        messages=[{",
    '            "role": "user",',
    '            "content": f"hello {i}",',
    '            "timestamp": started.isoformat().replace("+00:00", "Z"),',
    "        }],",
    "        timing={",
    '            "startedAt": started.isoformat().replace("+00:00", "Z"),',
    '            "endedAt": ended.isoformat().replace("+00:00", "Z"),',
    '            "latencyMs": 1000,',
    "        },",
    '        usage={"tokensIn": 10, "tokensOut": 5},',
    "        env=env,",
    "        trace_id=trace_id,",
    "    )",
    "    traces.append(trace)",
    "",
    "kwargs = {}",
    "if batch_id:",
    '    kwargs["batch_id"] = batch_id',
    "path = write_jsonl(traces, **kwargs)",
    "print(str(path))",
  ].join("\n");

  const res = runPythonScript(script, { registryPath: opts.registryPath });
  const lastLine = res.stdout.trim().split("\n").pop() ?? "";
  return {
    status: res.status,
    stdout: res.stdout,
    stderr: res.stderr,
    batchPath: lastLine,
  };
}
