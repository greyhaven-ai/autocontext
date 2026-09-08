/**
 * Run against an npm-installed Pi tarball and the published autoctx runtime.
 * The fixture must contain pi-autocontext and its Pi peers in node_modules,
 * with working native dependencies. No Vitest aliases or autoctx mocks apply.
 * Only the Pi host registration API is simulated; every tool uses real runtime
 * classes, the deterministic provider, and a migrated temporary SQLite database.
 */
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { constants, copyFileSync, cpSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const currentFile = fileURLToPath(import.meta.url);
const cleanEnv = {
  PATH: process.env.PATH,
  TMPDIR: tmpdir(),
  ...(process.env.SystemRoot ? { SystemRoot: process.env.SystemRoot } : {}),
};

// Reuse the graph from npm ci, including deliberately rebuilt native modules.
// Packing and unpacking the extension performs no fresh dependency resolution.
if (!process.argv[2]) {
  const sourceRoot = dirname(dirname(currentFile));
  const lockText = readFileSync(join(sourceRoot, "package-lock.json"), "utf8");
  const lock = JSON.parse(lockText);
  const version = lock.packages["node_modules/autoctx"].version;
  const fixture = mkdtempSync(join(tmpdir(), "pi-packed-runtime-"));
  let status;
  try {
    const artifacts = join(fixture, "artifacts");
    mkdirSync(artifacts);
    const packed = JSON.parse(execFileSync("npm", [
      "pack", "--ignore-scripts", "--json", "--pack-destination", artifacts,
      "--cache", join(fixture, "npm-cache"),
    ], { cwd: sourceRoot, env: cleanEnv, encoding: "utf8" }));
    assert.equal(packed.length, 1);
    cpSync(join(sourceRoot, "node_modules"), join(fixture, "node_modules"), {
      recursive: true,
      mode: constants.COPYFILE_FICLONE,
    });
    const extensionRoot = join(fixture, "node_modules", "pi-autocontext");
    mkdirSync(extensionRoot);
    execFileSync("tar", [
      "-xzf", join(artifacts, packed[0].filename), "--strip-components=1", "-C", extensionRoot,
    ], { env: cleanEnv });
    writeFileSync(join(fixture, "package.json"), JSON.stringify({ private: true, type: "module" }));
    assert.equal(readFileSync(join(sourceRoot, "package-lock.json"), "utf8"), lockText);
    console.log(`Packed ${packed[0].id}; checking locked autoctx ${version}`);
    const result = spawnSync(process.execPath, [currentFile, fixture, version], {
      env: cleanEnv,
      stdio: "inherit",
    });
    if (result.error) throw result.error;
    status = result.status ?? 1;
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
  process.exit(status);
}

const root = realpathSync(resolve(process.argv[2]));
const expectedVersion = process.argv[3] ?? "0.17.3";
// Run inside the installed fixture so native ESM package resolution selects its
// import-only autoctx and Pi exports, with no source-tree alias or mock.
if (dirname(currentFile) !== root) {
  const worker = join(root, `.pi-runtime-contract-${process.pid}.mjs`);
  copyFileSync(currentFile, worker);
  let status;
  try {
    const result = spawnSync(process.execPath, [worker, root, expectedVersion], {
      cwd: root,
      env: cleanEnv,
      stdio: "inherit",
    });
    if (result.error) throw result.error;
    status = result.status ?? 1;
  } finally {
    rmSync(worker, { force: true });
  }
  process.exit(status);
}
const workspace = mkdtempSync(join(tmpdir(), "pi-installed-contract-"));
const previousCwd = process.cwd();
try {
  process.chdir(workspace);
  process.env.AUTOCONTEXT_CONFIG_DIR = join(workspace, "empty-config");
  process.env.AUTOCONTEXT_PROVIDER = "deterministic";
  process.env.AUTOCONTEXT_AGENT_PROVIDER = "deterministic";
  process.env.AUTOCONTEXT_DB_PATH = join(workspace, "runtime.sqlite3");
  process.env.AUTOCONTEXT_EVENT_STREAM_PATH = join(workspace, "events.ndjson");
  process.env.AUTOCONTEXT_RUNS_ROOT = join(workspace, "runs");
  process.env.AUTOCONTEXT_KNOWLEDGE_ROOT = join(workspace, "knowledge");
  globalThis.fetch = () => { throw new Error("Contract tests must not use network providers"); };

  const ac = await import("autoctx");
  const runtimeRoot = dirname(fileURLToPath(import.meta.resolve("autoctx/package.json")));
  assert.equal(JSON.parse(readFileSync(join(runtimeRoot, "package.json"), "utf8")).version, expectedVersion);
  assert.ok(runtimeRoot.startsWith(join(root, "node_modules")));
  const extensionRequire = createRequire(join(root, "node_modules/pi-autocontext/package.json"));
  assert.equal(extensionRequire.resolve("autoctx/package.json"), join(runtimeRoot, "package.json"));
  const hostRequire = createRequire(import.meta.resolve("@earendil-works/pi-coding-agent"));
  const { createJiti } = hostRequire("jiti");
  const loader = createJiti(import.meta.url);
  const extension = await loader.import(join(root, "node_modules/pi-autocontext/src/index.ts"), { default: true });
  const tools = new Map();
  extension({ registerTool(tool) { tools.set(tool.name, tool); }, registerCommand() {}, on() {} });
  assert.equal(tools.size, 6);
  console.log("PASS packed extension registration with real installed Pi dependencies");

  const store = new ac.SQLiteStore(process.env.AUTOCONTEXT_DB_PATH);
  store.migrate(join(runtimeRoot, "migrations"));
  store.createRun("contract-run", "grid_ctf", 1, "local", "deterministic");
  store.close();
  writeFileSync(process.env.AUTOCONTEXT_EVENT_STREAM_PATH, JSON.stringify({ type: "contract-event", run_id: "contract-run" }) + "\n");
  const run = (name, params) => tools.get(name).execute("contract-call", params, undefined, undefined, { cwd: workspace });

  const scenarios = await run("autocontext_scenarios", {});
  assert.ok(scenarios.details.scenarios.includes("grid_ctf"));
  assert.equal(scenarios.details.count, Object.keys(ac.SCENARIO_REGISTRY).length);
  console.log("PASS scenarios from real published registry");

  const judge = await run("autocontext_judge", { task_prompt: "Write a clear answer", agent_output: "Initial answer", rubric: "Clear, accurate and actionable" });
  assert.equal(judge.details.score, 0.72);
  console.log("PASS judge via real deterministic provider and LLMJudge");

  const improve = await run("autocontext_improve", { task_prompt: "Write a clear answer", initial_output: "Initial answer", rubric: "Clear, accurate and actionable", max_rounds: 3, quality_threshold: 0.9 });
  assert.equal(improve.details.bestScore, 0.92);
  assert.equal(improve.details.rounds, 2);
  console.log("PASS real SimpleAgentTask and ImprovementLoop revision");

  const status = await run("autocontext_status", { run_id: "contract-run" });
  assert.equal(status.details.run_id, "contract-run");
  assert.equal((await run("autocontext_status", {})).details.count, 1);
  console.log("PASS status reads real migrated SQLiteStore");

  await run("autocontext_queue", { spec_name: "contract-task", task_prompt: "Queued prompt", rubric: "Queued rubric", priority: 7 });
  const checkStore = new ac.SQLiteStore(process.env.AUTOCONTEXT_DB_PATH);
  const [queued] = checkStore.listTasks();
  assert.equal(queued.spec_name, "contract-task");
  assert.equal(queued.priority, 7);
  const config = typeof queued.config_json === "string" ? JSON.parse(queued.config_json) : queued.config;
  assert.equal(config.task_prompt, "Queued prompt");
  assert.equal(config.rubric, "Queued rubric");
  checkStore.close();
  console.log("PASS real enqueueTask persists overrides and priority");

  const snapshot = await run("autocontext_runtime_snapshot", { run_id: "contract-run", limit: 10 });
  assert.equal(snapshot.details.format, "autocontext.runtime_snapshot.v1");
  assert.equal(snapshot.details.run.run_id, "contract-run");
  assert.equal(snapshot.details.events[0].type, "contract-event");
  assert.deepEqual(snapshot.details.unavailable, []);
  assert.deepEqual(snapshot.details.sessions, []);
  console.log("PASS real runtime snapshot, session schema and event stream");
  console.log(JSON.stringify({ runtime: runtimeRoot, toolContracts: 6, node: process.version, abi: process.versions.modules }));

} finally {
  process.chdir(previousCwd);
  rmSync(workspace, { recursive: true, force: true });
}
