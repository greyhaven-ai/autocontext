import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { hostname, tmpdir } from "node:os";
import { performance } from "node:perf_hooks";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildPersistedAgentTaskSpecData,
  persistAgentTaskScenario,
} from "../src/scenarios/agent-task-persistence-workflow.js";
import type { AgentTaskSpec } from "../src/scenarios/agent-task-spec.js";
import { getScenarioTypeMarker } from "../src/scenarios/families.js";
import { resolveCustomAgentTask } from "../src/scenarios/custom-loader.js";
import {
  loadPrivateEvaluatorContext,
  persistPrivateEvaluatorContext,
  prunePrivateEvaluatorContexts,
  rehydratePersistedEvaluatorContext,
  withPrivateEvaluatorContextWriteLock,
} from "../src/scenarios/private-evaluator-context-store.js";
import { persistMaterializedScenarioArtifacts } from "../src/scenarios/materialize-artifact-persistence.js";

describe("agent task persistence workflow", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-agent-task-persist-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("builds persisted spec data and writes custom scenario files", () => {
    const spec: AgentTaskSpec = {
      improvementTaskContractVersion: 1,
      taskPrompt: "Write about RLMs",
      judgeRubric: "Check accuracy",
      outputFormat: "free_text",
      judgeModel: "gpt-5.6-luna",
      referenceContext: "RLM = Recursive Language Model",
      evaluationContext: "PRIVATE_EVALUATOR_SENTINEL",
      referenceSources: ["https://example.com/rlm"],
      requiredConcepts: ["context folding"],
      maxRounds: 3,
      qualityThreshold: 0.95,
      revisionPrompt: "Improve the draft",
      sampleInput: "topic=rlm",
    };

    expect(buildPersistedAgentTaskSpecData(spec)).toMatchObject({
      improvement_task_contract_version: 1,
      task_prompt: "Write about RLMs",
      judge_rubric: "Check accuracy",
      output_format: "free_text",
      judge_model: "gpt-5.6-luna",
      reference_context: "RLM = Recursive Language Model",
      max_rounds: 3,
      quality_threshold: 0.95,
      revision_prompt: "Improve the draft",
      sample_input: "topic=rlm",
    });

    const scenarioDir = persistAgentTaskScenario({
      knowledgeRoot: dir,
      name: "recursive_language_models",
      spec,
    });

    expect(existsSync(join(scenarioDir, "agent_task_spec.json"))).toBe(true);
    expect(existsSync(join(scenarioDir, "scenario_type.txt"))).toBe(true);
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8")).toBe(
      getScenarioTypeMarker("agent_task"),
    );
    expect(
      JSON.parse(readFileSync(join(scenarioDir, "agent_task_spec.json"), "utf-8")),
    ).toMatchObject({
      improvement_task_contract_version: 1,
      task_prompt: "Write about RLMs",
      required_concepts: ["context folding"],
      evaluation_context_ref: expect.stringMatching(/^sha256:[a-f0-9]{64}$/),
    });
    const publicSpec = readFileSync(join(scenarioDir, "agent_task_spec.json"), "utf-8");
    expect(publicSpec).not.toContain("PRIVATE_EVALUATOR_SENTINEL");
    expect(resolveCustomAgentTask(dir, "recursive_language_models")?.spec.evaluationContext).toBe(
      "PRIVATE_EVALUATOR_SENTINEL",
    );
  });

  it("canonically removes stale public refs and private records on eval-to-none overwrite", () => {
    const scenarioName = "eval_to_none_rewrite";
    const baseSpec: AgentTaskSpec = {
      taskPrompt: "Rewrite safely.",
      judgeRubric: "Evaluate safety.",
      outputFormat: "free_text",
      judgeModel: "",
      evaluationContext: "OLD_PRIVATE_CONTEXT",
      maxRounds: 1,
      qualityThreshold: 0.9,
    };
    const scenarioDir = join(dir, "_custom_scenarios", scenarioName);
    persistMaterializedScenarioArtifacts({
      scenarioDir,
      scenarioType: "agent_task",
      persistedSpec: {
        name: scenarioName,
        family: "agent_task",
        evaluationContext: baseSpec.evaluationContext,
      },
      family: "agent_task",
      agentTaskFamily: "agent_task",
      agentTaskSpec: baseSpec,
      source: null,
    });
    const oldPublic = JSON.parse(
      readFileSync(join(scenarioDir, "agent_task_spec.json"), "utf8"),
    ) as Record<string, unknown>;
    const oldReference = String(oldPublic.evaluation_context_ref);
    writeFileSync(join(scenarioDir, "scenario.js"), "stale generated source", "utf8");

    persistAgentTaskScenario({
      knowledgeRoot: dir,
      name: scenarioName,
      spec: {
        ...baseSpec,
        taskPrompt: "Rewrite without private evaluation.",
        evaluationContext: null,
      },
    });

    expect(existsSync(join(scenarioDir, "spec.json"))).toBe(false);
    expect(existsSync(join(scenarioDir, "scenario.js"))).toBe(false);
    const rewrittenPublic = readFileSync(join(scenarioDir, "agent_task_spec.json"), "utf8");
    expect(rewrittenPublic).not.toContain("evaluation_context_ref");
    expect(rewrittenPublic).not.toContain("OLD_PRIVATE_CONTEXT");
    expect(() =>
      loadPrivateEvaluatorContext({
        knowledgeRoot: dir,
        scenarioName,
        reference: oldReference,
      }),
    ).toThrow(/private evaluator context is missing/i);
    expect(resolveCustomAgentTask(dir, scenarioName)?.spec).toMatchObject({
      taskPrompt: "Rewrite without private evaluation.",
      evaluationContext: null,
    });
  });

  it("fails closed when a private record exists without a public evaluator reference", () => {
    persistPrivateEvaluatorContext({
      knowledgeRoot: dir,
      scenarioName: "orphaned_private_context",
      evaluationContext: "ORPHANED_PRIVATE_SENTINEL",
    });

    expect(() =>
      rehydratePersistedEvaluatorContext({
        knowledgeRoot: dir,
        scenarioName: "orphaned_private_context",
        persistedSpec: { task_prompt: "Do work", judge_rubric: "Judge work" },
      }),
    ).toThrow(/orphaned private evaluator context/i);
  });

  it("removes interrupted temporary records when pruning evaluator context", () => {
    persistPrivateEvaluatorContext({
      knowledgeRoot: dir,
      scenarioName: "interrupted_private_write",
      evaluationContext: "PRIVATE_SENTINEL",
    });
    const storeRoot = join(dir, "_private_evaluator_context");
    const scenarioStore = join(storeRoot, readdirSync(storeRoot)[0]!);
    writeFileSync(join(scenarioStore, "interrupted.tmp"), "PRIVATE_TEMP_SENTINEL", "utf8");

    prunePrivateEvaluatorContexts({
      knowledgeRoot: dir,
      scenarioName: "interrupted_private_write",
    });

    expect(existsSync(scenarioStore)).toBe(false);
  });

  it("rejects concurrent private evaluator artifact writers for one scenario", () => {
    expect(() =>
      withPrivateEvaluatorContextWriteLock({
        knowledgeRoot: dir,
        scenarioName: "concurrent_private_write",
        write: () =>
          withPrivateEvaluatorContextWriteLock({
            knowledgeRoot: dir,
            scenarioName: "concurrent_private_write",
            write: () => undefined,
          }),
      }),
    ).toThrow(/update is already in progress/i);
  });

  it("keeps an ambiguous plain-v1 owner fail closed", async () => {
    const scenarioName = "legacy_lock_recovery";
    const scenarioKey = createHash("sha256")
      .update(`scenario:${scenarioName}`, "utf8")
      .digest("hex");
    const lockRoot = join(dir, "_private_evaluator_context_locks");
    const lockPath = join(lockRoot, `${scenarioKey}.lock`);
    const deadOwner = spawn(process.execPath, ["-e", ""], { stdio: "ignore" });
    const deadPid = deadOwner.pid;
    if (!deadPid) throw new Error("failed to start legacy lock owner fixture");
    await once(deadOwner, "exit");
    mkdirSync(lockRoot, { recursive: true });
    writeFileSync(
      lockPath,
      JSON.stringify({
        version: 1,
        token: "legacy-dead-owner",
        pid: deadPid,
        hostname: hostname(),
        createdAtMs: Date.now(),
      }),
      { mode: 0o600 },
    );

    let entered = false;
    const monotonicNow = vi
      .spyOn(performance, "now")
      .mockReturnValueOnce(0)
      .mockReturnValue(6_000);
    try {
      expect(() =>
        withPrivateEvaluatorContextWriteLock({
          knowledgeRoot: dir,
          scenarioName,
          write: () => {
            entered = true;
          },
        }),
      ).toThrow(/update is already in progress/i);
    } finally {
      monotonicNow.mockRestore();
    }

    expect(entered).toBe(false);
    expect(existsSync(lockPath)).toBe(true);
  });

  it("keeps an unreadable owner record fail closed", () => {
    const scenarioName = "unreadable_lock_owner";
    const scenarioKey = createHash("sha256")
      .update(`scenario:${scenarioName}`, "utf8")
      .digest("hex");
    const lockRoot = join(dir, "_private_evaluator_context_locks");
    const lockPath = join(lockRoot, `${scenarioKey}.lock`);
    mkdirSync(lockRoot, { recursive: true });
    writeFileSync(lockPath, "{malformed", { mode: 0o600 });
    const monotonicNow = vi
      .spyOn(performance, "now")
      .mockReturnValueOnce(0)
      .mockReturnValue(6_000);
    try {
      expect(() =>
        withPrivateEvaluatorContextWriteLock({
          knowledgeRoot: dir,
          scenarioName,
          write: () => undefined,
        }),
      ).toThrow(/update is already in progress/i);
    } finally {
      monotonicNow.mockRestore();
    }
    expect(readFileSync(lockPath, "utf8")).toBe("{malformed");
  });

  it("recovers a dead owner written by the scoped-v1 schema", async () => {
    if (process.platform !== "linux") return;
    const scenarioName = "scoped_legacy_lock_recovery";
    const scenarioKey = createHash("sha256")
      .update(`scenario:${scenarioName}`, "utf8")
      .digest("hex");
    const lockPath = join(
      dir,
      "_private_evaluator_context_locks",
      `${scenarioKey}.lock`,
    );
    let capturedOwner: Record<string, unknown> | undefined;
    withPrivateEvaluatorContextWriteLock({
      knowledgeRoot: dir,
      scenarioName,
      write: () => {
        capturedOwner = JSON.parse(readFileSync(lockPath, "utf8")) as Record<string, unknown>;
      },
    });
    if (!capturedOwner || typeof capturedOwner.legacyScopeId !== "string") {
      throw new Error("failed to capture legacy lock scope");
    }
    const deadOwner = spawn(process.execPath, ["-e", ""], { stdio: "ignore" });
    const deadPid = deadOwner.pid;
    if (!deadPid) throw new Error("failed to start scoped legacy owner fixture");
    await once(deadOwner, "exit");
    writeFileSync(
      lockPath,
      JSON.stringify({
        version: 1,
        token: "scoped-legacy-dead-owner",
        pid: deadPid,
        hostname: hostname(),
        scopeId: capturedOwner.legacyScopeId,
        processStartId: "linux-start-ticks:0",
        createdAtMs: Date.now(),
      }),
      { mode: 0o600 },
    );

    let entered = false;
    withPrivateEvaluatorContextWriteLock({
      knowledgeRoot: dir,
      scenarioName,
      write: () => {
        entered = true;
      },
    });

    expect(entered).toBe(true);
    expect(existsSync(lockPath)).toBe(false);
  });

  it("recovers a same-machine lock from a prior boot", () => {
    const scenarioName = "prior_boot_lock_recovery";
    const scenarioKey = createHash("sha256")
      .update(`scenario:${scenarioName}`, "utf8")
      .digest("hex");
    const lockPath = join(
      dir,
      "_private_evaluator_context_locks",
      `${scenarioKey}.lock`,
    );
    let capturedOwner: Record<string, unknown> | undefined;
    withPrivateEvaluatorContextWriteLock({
      knowledgeRoot: dir,
      scenarioName,
      write: () => {
        capturedOwner = JSON.parse(readFileSync(lockPath, "utf8")) as Record<string, unknown>;
      },
    });
    if (!capturedOwner) throw new Error("failed to capture current lock owner");
    // Platforms that cannot expose a stable per-boot token deliberately keep
    // mismatched boot identities fail closed rather than guessing from wall time.
    if (capturedOwner.machineIdReliable !== true || capturedOwner.bootIdReliable !== true) return;
    const replacementBootId = capturedOwner.bootId === "0".repeat(64)
      ? "1".repeat(64)
      : "0".repeat(64);
    writeFileSync(
      lockPath,
      JSON.stringify({
        ...capturedOwner,
        token: "prior-boot-owner",
        bootId: replacementBootId,
      }),
      { mode: 0o600 },
    );

    let entered = false;
    withPrivateEvaluatorContextWriteLock({
      knowledgeRoot: dir,
      scenarioName,
      write: () => {
        entered = true;
      },
    });

    expect(entered).toBe(true);
    expect(existsSync(lockPath)).toBe(false);
  });

  it("sweeps crash artifacts after takeover renamed the stale owner", () => {
    const scenarioName = "post_rename_crash_cleanup";
    const scenarioKey = createHash("sha256")
      .update(`scenario:${scenarioName}`, "utf8")
      .digest("hex");
    const lockRoot = join(dir, "_private_evaluator_context_locks");
    const lockPath = join(lockRoot, `${scenarioKey}.lock`);
    const stalePath = `${lockPath}.stale.crashed-recoverer`;
    const recoveryRoot = `${lockPath}.recovery.crashed-recoverer`;
    mkdirSync(stalePath, { recursive: true });
    mkdirSync(recoveryRoot, { recursive: true });
    writeFileSync(join(stalePath, "owner.json"), "stale", "utf8");
    writeFileSync(join(recoveryRoot, "1.claim"), "claim", "utf8");

    withPrivateEvaluatorContextWriteLock({
      knowledgeRoot: dir,
      scenarioName,
      write: () => {
        expect(existsSync(lockPath)).toBe(true);
        expect(existsSync(stalePath)).toBe(false);
        expect(existsSync(recoveryRoot)).toBe(false);
      },
    });

    expect(existsSync(lockPath)).toBe(false);
    expect(
      readdirSync(lockRoot).filter((entry) => entry.startsWith(`${scenarioKey}.lock.`)),
    ).toEqual([]);
  });

  it("fences stale-lock recovery across two concurrent processes", async () => {
    const scenarioName = "concurrent_stale_lock_recovery";
    const scenarioKey = createHash("sha256")
      .update(`scenario:${scenarioName}`, "utf8")
      .digest("hex");
    const lockPath = join(
      dir,
      "_private_evaluator_context_locks",
      `${scenarioKey}.lock`,
    );
    const criticalPath = join(dir, "exclusive-critical-section");
    const entryLog = join(dir, "critical-section-entries.txt");
    const startGate = join(dir, "start-contenders");
    const recoveryGate = join(dir, "release-recovery-snapshots");
    const snapshotPaths = ["a", "b"].map((id) => join(dir, `snapshot-${id}`));
    const lockModuleUrl = new URL(
      "../src/scenarios/private-evaluator-context-store.ts",
      import.meta.url,
    ).href;
    const childArgs = (script: string) => [
      "--import",
      "tsx/esm",
      "--input-type=module",
      "-e",
      script,
    ];
    const staleOwnerScript = `
      import { withPrivateEvaluatorContextWriteLock } from ${JSON.stringify(lockModuleUrl)};
      withPrivateEvaluatorContextWriteLock({
        knowledgeRoot: ${JSON.stringify(dir)},
        scenarioName: ${JSON.stringify(scenarioName)},
        write: () => {
          process.stdout.write("READY\\n");
          Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0);
        },
      });
    `;
    const staleOwner = spawn(process.execPath, childArgs(staleOwnerScript), {
      stdio: ["ignore", "pipe", "inherit"],
    });
    const staleOwnerExit = once(staleOwner, "exit");

    try {
      const [ready] = await once(staleOwner.stdout!, "data");
      expect(String(ready)).toContain("READY");
      expect(staleOwner.kill("SIGKILL")).toBe(true);
      await staleOwnerExit;
    } finally {
      if (staleOwner.exitCode === null && staleOwner.signalCode === null) {
        staleOwner.kill("SIGKILL");
      }
    }

    const contenderScript = (id: string, snapshotPath: string) => `
      import * as fs from "node:fs";
      import { channel } from "node:diagnostics_channel";
      const recoverySnapshots = channel(
        "autoctx.private-evaluator-context.write-lock.recovery-snapshot",
      );
      recoverySnapshots.subscribe((message) => {
        if (message.lockPath !== ${JSON.stringify(lockPath)}) return;
        fs.writeFileSync(${JSON.stringify(snapshotPath)}, message.identity, "utf8");
        const snapshotWait = new Int32Array(new SharedArrayBuffer(4));
        while (!fs.existsSync(${JSON.stringify(recoveryGate)})) {
          Atomics.wait(snapshotWait, 0, 0, 10);
        }
      });
      const { withPrivateEvaluatorContextWriteLock } = await import(
        ${JSON.stringify(lockModuleUrl)}
      );
      process.stdout.write("READY\\n");
      const wait = new Int32Array(new SharedArrayBuffer(4));
      while (!fs.existsSync(${JSON.stringify(startGate)})) Atomics.wait(wait, 0, 0, 10);
      withPrivateEvaluatorContextWriteLock({
        knowledgeRoot: ${JSON.stringify(dir)},
        scenarioName: ${JSON.stringify(scenarioName)},
        write: () => {
          fs.mkdirSync(${JSON.stringify(criticalPath)});
          try {
            fs.appendFileSync(${JSON.stringify(entryLog)}, ${JSON.stringify(`${id}\n`)});
            Atomics.wait(wait, 0, 0, 200);
          } finally {
            fs.rmSync(${JSON.stringify(criticalPath)}, { recursive: true, force: true });
          }
        },
      });
    `;
    const contenders = ["a", "b"].map((id, index) =>
      spawn(process.execPath, childArgs(contenderScript(id, snapshotPaths[index]!)), {
        stdio: ["ignore", "pipe", "pipe"],
      }),
    );
    const errors = ["", ""];
    contenders.forEach((child, index) => {
      child.stderr!.on("data", (chunk) => {
        errors[index] += String(chunk);
      });
    });
    const exits = contenders.map((child) => once(child, "exit"));

    try {
      await Promise.all(contenders.map((child) => once(child.stdout!, "data")));
      writeFileSync(startGate, "go", "utf8");
      const snapshotDeadline = Date.now() + 5_000;
      while (snapshotPaths.some((path) => !existsSync(path))) {
        if (Date.now() >= snapshotDeadline) {
          throw new Error(`contenders did not snapshot the stale owner: ${errors.join("\n")}`);
        }
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      expect(new Set(snapshotPaths.map((path) => readFileSync(path, "utf8"))).size).toBe(1);
      writeFileSync(recoveryGate, "go", "utf8");
      const results = await Promise.all(exits);
      expect(
        results.map(([code, signal], index) => ({ code, signal, error: errors[index] })),
      ).toEqual([
        { code: 0, signal: null, error: "" },
        { code: 0, signal: null, error: "" },
      ]);
    } finally {
      for (const child of contenders) {
        if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
      }
    }

    expect(readFileSync(entryLog, "utf8").trim().split("\n").sort()).toEqual(["a", "b"]);
    expect(existsSync(criticalPath)).toBe(false);
    expect(existsSync(lockPath)).toBe(false);
    expect(
      readdirSync(join(dir, "_private_evaluator_context_locks"))
        .filter((entry) => entry.startsWith(`${scenarioKey}.lock.`)),
    ).toEqual([]);
  });

  it("recovers a SIGKILL-stranded public ref, private temp, and writer lock", async () => {
    const scenarioName = "sigkill_private_recovery";
    const evaluationContext = "RECOVERED_PRIVATE_SENTINEL";
    const scenarioKey = createHash("sha256")
      .update(`scenario:${scenarioName}`, "utf8")
      .digest("hex");
    const contextDigest = createHash("sha256")
      .update(evaluationContext, "utf8")
      .digest("hex");
    const reference = `sha256:${contextDigest}`;
    const scenarioDir = join(dir, "_custom_scenarios", scenarioName);
    const privateDir = join(dir, "_private_evaluator_context", scenarioKey);
    const lockDir = join(dir, "_private_evaluator_context_locks");
    const lockPath = join(lockDir, `${scenarioKey}.lock`);
    const childScript = `
      import * as fs from "node:fs";
      import { withPrivateEvaluatorContextWriteLock } from ${JSON.stringify(
        new URL("../src/scenarios/private-evaluator-context-store.ts", import.meta.url).href,
      )};
      withPrivateEvaluatorContextWriteLock({
        knowledgeRoot: ${JSON.stringify(dir)},
        scenarioName: ${JSON.stringify(scenarioName)},
        write: () => {
          fs.mkdirSync(${JSON.stringify(scenarioDir)}, { recursive: true });
          fs.writeFileSync(
            ${JSON.stringify(join(scenarioDir, "agent_task_spec.json"))},
            JSON.stringify({
              task_prompt: "Recover this task",
              judge_rubric: "Judge recovery",
              output_format: "free_text",
              judge_model: "",
              evaluation_context_ref: ${JSON.stringify(reference)},
            }),
          );
          fs.writeFileSync(
            ${JSON.stringify(join(scenarioDir, "scenario_type.txt"))},
            ${JSON.stringify(getScenarioTypeMarker("agent_task"))},
          );
          fs.mkdirSync(${JSON.stringify(privateDir)}, { recursive: true });
          fs.writeFileSync(
            ${JSON.stringify(join(privateDir, `${contextDigest}.json.crashed.tmp`))},
            JSON.stringify({
              version: 1,
              scenarioName: ${JSON.stringify(scenarioName)},
              evaluationContext: ${JSON.stringify(evaluationContext)},
            }),
            { mode: 0o600 },
          );
          process.stdout.write("READY\\n");
          Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0);
        },
      });
    `;
    const child = spawn(
      process.execPath,
      ["--import", "tsx/esm", "--input-type=module", "-e", childScript],
      {
        stdio: ["ignore", "pipe", "inherit"],
      },
    );

    try {
      const [ready] = await once(child.stdout!, "data");
      expect(String(ready)).toContain("READY");
      expect(child.kill("SIGKILL")).toBe(true);
      await once(child, "exit");
    } finally {
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
    }

    persistAgentTaskScenario({
      knowledgeRoot: dir,
      name: scenarioName,
      spec: {
        taskPrompt: "Recover this task",
        judgeRubric: "Judge recovery",
        outputFormat: "free_text",
        judgeModel: "",
        evaluationContext,
        maxRounds: 1,
        qualityThreshold: 0.9,
      },
    });

    expect(resolveCustomAgentTask(dir, scenarioName)?.spec.evaluationContext).toBe(
      evaluationContext,
    );
    expect(readdirSync(privateDir)).toEqual([`${contextDigest}.json`]);
    expect(existsSync(lockPath)).toBe(false);
    expect(
      readdirSync(lockDir).filter((entry) => entry.startsWith(`${scenarioKey}.lock.`)),
    ).toEqual([]);
  });
});
