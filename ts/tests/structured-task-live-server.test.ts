import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { WebSocket } from "ws";

import { asDbPath } from "../src/domain/ids.js";
import { DeterministicProvider } from "../src/providers/deterministic.js";
import { InteractiveServer, RunManager } from "../src/server/index.js";
import { SQLiteStore } from "../src/storage/index.js";
import { resolveCustomAgentTask } from "../src/scenarios/custom-loader.js";

type WireMessage = Record<string, unknown>;

interface BufferedWebSocket {
  readonly messages: WireMessage[];
  send(message: WireMessage): void;
  waitFor(predicate: (message: WireMessage) => boolean, timeoutMs?: number): Promise<WireMessage>;
  close(): void;
  waitForClose(): Promise<void>;
}

async function openBufferedWebSocket(url: string): Promise<BufferedWebSocket> {
  const socket = new WebSocket(url);
  const messages: WireMessage[] = [];
  const subscribers = new Set<(message: WireMessage) => void>();
  const closed = new Promise<void>((resolve) => {
    socket.once("close", () => resolve());
  });

  socket.on("message", (data) => {
    const message = JSON.parse(data.toString()) as WireMessage;
    messages.push(message);
    for (const subscriber of [...subscribers]) {
      subscriber(message);
    }
  });

  await new Promise<void>((resolve, reject) => {
    socket.once("open", resolve);
    socket.once("error", reject);
  });

  return {
    messages,
    send(message) {
      socket.send(JSON.stringify(message));
    },
    waitFor(predicate, timeoutMs = 15_000) {
      const existing = messages.find(predicate);
      if (existing) return Promise.resolve(existing);

      return new Promise<WireMessage>((resolve, reject) => {
        const subscriber = (message: WireMessage) => {
          if (!predicate(message)) return;
          clearTimeout(timer);
          subscribers.delete(subscriber);
          resolve(message);
        };
        const timer = setTimeout(() => {
          subscribers.delete(subscriber);
          reject(new Error(`Timed out waiting for WebSocket message from ${url}`));
        }, timeoutMs);
        subscribers.add(subscriber);
      });
    },
    close() {
      socket.close();
    },
    waitForClose() {
      return closed;
    },
  };
}

function sourceDescriptor(input: {
  id: string;
  role: "target" | "eval";
  name: string;
  contentRef: string;
  content: string;
}): Record<string, unknown> {
  const bytes = Buffer.from(input.content, "utf8");
  return {
    schemaVersion: 1,
    id: input.id,
    role: input.role,
    name: input.name,
    contentRef: input.contentRef,
    mediaType: "text/csv",
    provenance: {
      origin: "integration-test",
      metadata: { retained_for: "mission" },
    },
    integrity: {
      contentHash: `sha256:${createHash("sha256").update(bytes).digest("hex")}`,
      byteLength: bytes.byteLength,
      truncated: false,
    },
  };
}

function eventMessages(socket: BufferedWebSocket, event: string): WireMessage[] {
  return socket.messages.filter((message) => message.type === "event" && message.event === event);
}

function eventPayload(message: WireMessage): Record<string, unknown> {
  return message.payload as Record<string, unknown>;
}

describe("structured task live server", () => {
  it(
    "runs a structured target and evaluator dataset through setup, improvement, artifacts, and SQLite",
    { timeout: 30_000 },
    async () => {
      const root = mkdtempSync(join(tmpdir(), "autoctx-structured-live-"));
      const dbPath = join(root, "autocontext.db");
      const runsRoot = join(root, "runs");
      const knowledgeRoot = join(root, "knowledge");
      const migrationsDir = join(import.meta.dirname, "..", "migrations");
      const provider = new DeterministicProvider();
      const manager = new RunManager({
        dbPath,
        migrationsDir,
        runsRoot,
        knowledgeRoot,
        providerType: "deterministic",
        deps: {
          resolveProviderBundle: () => ({
            defaultProvider: provider,
            defaultConfig: {
              providerType: "deterministic",
              apiKey: "",
              baseUrl: "",
              model: provider.defaultModel(),
            },
            roleProviders: {},
            roleModels: {},
          }),
        },
      });
      const server = new InteractiveServer({ runManager: manager, port: 0 });
      let socket: BufferedWebSocket | null = null;
      let store: SQLiteStore | null = null;

      const targetContent = [
        "issue_id,title,status",
        "AC-101,Clarify setup progress,open",
        "AC-102,Retain final artifacts,open",
      ].join("\n");
      const evaluatorContent = [
        "criterion,expected",
        "grounding,Every recommendation maps to an issue_id",
        "privacy,EVAL_ONLY_SENTINEL_7F8A must never appear in the result",
      ].join("\n");
      const target = sourceDescriptor({
        id: "mission-target",
        role: "target",
        name: "Linear issues.csv",
        contentRef: "mission://linear/issues.csv",
        content: targetContent,
      });
      const evaluator = sourceDescriptor({
        id: "mission-evaluator",
        role: "eval",
        name: "Evaluator cases.csv",
        contentRef: "mission://linear/evaluator.csv",
        content: evaluatorContent,
      });

      try {
        await server.start();
        socket = await openBufferedWebSocket(`${server.url}?transcript_protocol_version=1`);

        const hello = await socket.waitFor((message) => message.type === "hello");
        expect(hello).toMatchObject({
          protocol_version: 2,
          transcript_protocol_version: 1,
        });
        expect(hello.capabilities).toEqual(expect.arrayContaining(["structured_task_creation_v1"]));
        await socket.waitFor((message) => message.type === "environments");
        await socket.waitFor((message) => message.type === "state");

        const createTaskMessage = {
          type: "create_task",
          contract: {
            schemaVersion: 1,
            objective: "Turn the attached issue data into a grounded product-improvement thesis.",
            target: "Improve the issue-derived thesis without inventing evidence.",
            deliverable: {
              description: "A concise thesis with evidence-backed recommendations and next steps.",
              outputFormat: "free_text",
            },
            dataSources: [target, evaluator],
            criteria:
              "Score evidence grounding, clarity, and actionability. Penalize unsupported claims.",
            qualityThreshold: 0.99,
            iterations: 2,
          },
          source_contents: [
            { sourceId: "mission-target", content: targetContent },
            { sourceId: "mission-evaluator", content: evaluatorContent },
          ],
        };

        socket.send({
          ...createTaskMessage,
          source_contents: [
            { sourceId: "mission-target", content: `${targetContent}\ntampered` },
            { sourceId: "mission-evaluator", content: evaluatorContent },
          ],
        });
        await socket.waitFor(
          (message) => message.type === "scenario_generating" && message.name === "improvement_task",
        );
        const setupFailure = await socket.waitFor(
          (message) => message.type === "scenario_error",
        );
        expect(setupFailure).toMatchObject({ stage: "validation" });
        expect(String(setupFailure.message)).toMatch(/byteLength mismatch|contentHash mismatch/);
        expect(socket.messages.some((message) => message.type === "scenario_preview")).toBe(false);

        socket.send(createTaskMessage);

        const generating = await socket.waitFor(
          (message) => message.type === "scenario_generating",
        );
        expect(generating).toMatchObject({ name: "improvement_task" });
        const preview = await socket.waitFor((message) => message.type === "scenario_preview");
        const scenarioName = String(preview.name);
        expect(scenarioName).toMatch(/^[a-z][a-z0-9_]*$/);

        socket.send({ type: "confirm_scenario" });
        await expect(
          socket.waitFor(
            (message) => message.type === "ack" && message.action === "confirm_scenario",
          ),
        ).resolves.toBeDefined();
        await expect(
          socket.waitFor(
            (message) => message.type === "scenario_ready" && message.name === scenarioName,
          ),
        ).resolves.toBeDefined();

        socket.send({
          type: "start_run",
          scenario: scenarioName,
          generations: 2,
          client_run_id: "structured-live-client",
          command_id: "structured-live-start",
        });

        const accepted = await socket.waitFor((message) => message.type === "run_accepted");
        expect(accepted).toMatchObject({
          client_run_id: "structured-live-client",
          command_id: "structured-live-start",
          scenario: scenarioName,
          generations: 2,
        });
        const runId = String(accepted.run_id);

        const completed = await socket.waitFor(
          (message) => message.type === "event" && message.event === "run_completed",
        );
        expect(eventPayload(completed)).toMatchObject({
          run_id: runId,
          completed_generations: 2,
          best_score: 0.92,
        });

        const lifecycle = socket.messages
          .filter(
            (message) =>
              message.type === "event" &&
              (message.event === "generation_started" || message.event === "generation_completed"),
          )
          .map((message) => ({
            event: message.event,
            generation: eventPayload(message).generation,
          }));
        expect(lifecycle).toEqual([
          { event: "generation_started", generation: 1 },
          { event: "generation_completed", generation: 1 },
          { event: "generation_started", generation: 2 },
          { event: "generation_completed", generation: 2 },
        ]);

        const progressTexts = eventMessages(socket, "agent_progress_note").map((message) =>
          String(eventPayload(message).text),
        );
        expect(progressTexts).toEqual(
          expect.arrayContaining([
            "Preparing the task context for drafting.",
            "Drafting the initial response.",
            "Evaluating the current response in round 1.",
            "Evaluation round 1 scored the current response with a score of 0.720.",
            "Revising the response after evaluation round 1.",
            "Evaluating the current response in round 2.",
            "Evaluation round 2 scored the current response with a score of 0.920.",
            "Retained the best result from 2 completed evaluation rounds with a best score of 0.920.",
          ]),
        );

        const action = eventMessages(socket, "action_detail").at(-1);
        expect(action).toBeDefined();
        expect(eventPayload(action!)).toMatchObject({
          run_id: runId,
          action_id: "agent-final-result",
          status: "completed",
          generation: 2,
          artifacts: [
            expect.objectContaining({
              id: "agent-final-output",
              name: "Final result.md",
              media_type: "text/markdown",
            }),
          ],
        });
        expect(JSON.stringify(eventPayload(action!))).not.toContain("EVAL_ONLY_SENTINEL_7F8A");

        const finalProgress = eventMessages(socket, "agent_progress_note").at(-1);
        expect(eventPayload(finalProgress!)).toMatchObject({
          run_id: runId,
          generation: 2,
          kind: "decision",
          evidence_targets: [
            {
              kind: "artifact",
              action_id: "agent-final-result",
              artifact_id: "agent-final-output",
            },
          ],
        });

        const persistedSpec = JSON.parse(
          readFileSync(
            join(knowledgeRoot, "_custom_scenarios", scenarioName, "agent_task_spec.json"),
            "utf8",
          ),
        ) as Record<string, unknown>;
        expect(persistedSpec.improvement_task_contract_version).toBe(1);
        expect(persistedSpec.task_data_sources).toEqual([target, evaluator]);
        expect(persistedSpec.sample_input).toContain(targetContent);
        expect(persistedSpec.sample_input).not.toContain("EVAL_ONLY_SENTINEL_7F8A");
        expect(persistedSpec.evaluation_context_ref).toMatch(/^sha256:[a-f0-9]{64}$/);
        expect(JSON.stringify(persistedSpec)).not.toContain(evaluatorContent);
        expect(resolveCustomAgentTask(knowledgeRoot, scenarioName)?.spec.evaluationContext).toContain(
          evaluatorContent,
        );
        expect(persistedSpec.reference_sources).toEqual(["mission://linear/issues.csv"]);

        store = new SQLiteStore(asDbPath(dbPath));
        const run = store.getRun(runId);
        expect(run).toMatchObject({
          run_id: runId,
          scenario: scenarioName,
          target_generations: 2,
          executor_mode: "agent_task",
          status: "completed",
          agent_provider: "deterministic",
        });

        const generations = store.getGenerations(runId);
        expect(generations).toHaveLength(2);
        expect(generations).toEqual([
          expect.objectContaining({
            generation_index: 1,
            mean_score: 0.72,
            best_score: 0.72,
            status: "completed",
            scoring_backend: "agent_task",
          }),
          expect.objectContaining({
            generation_index: 2,
            mean_score: 0.92,
            best_score: 0.92,
            status: "completed",
            scoring_backend: "agent_task",
          }),
        ]);
        expect(store.getAgentOutputs(runId, 1).map((row) => row.role)).toEqual([
          "competitor",
          "analyst",
        ]);
        expect(store.getAgentOutputs(runId, 2).map((row) => row.role)).toEqual([
          "competitor",
          "analyst",
          "coach",
        ]);
        expect(
          store.getAgentOutputs(runId, 2).find((row) => row.role === "competitor")?.content,
        ).not.toContain("EVAL_ONLY_SENTINEL_7F8A");
        expect(JSON.stringify(store.getAgentOutputs(runId, 1))).not.toContain(
          "EVAL_ONLY_SENTINEL_7F8A",
        );
        expect(JSON.stringify(store.getAgentOutputs(runId, 2))).not.toContain(
          "EVAL_ONLY_SENTINEL_7F8A",
        );
        expect(JSON.stringify(socket.messages)).not.toContain("EVAL_ONLY_SENTINEL_7F8A");
      } finally {
        store?.close();
        socket?.close();
        await server.stop();
        rmSync(root, { recursive: true, force: true });
      }
    },
  );

  it("advertises task creation on base protocol v2 and keeps schema failures recoverable", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-structured-validation-"));
    const manager = new RunManager({
      dbPath: join(root, "autocontext.db"),
      migrationsDir: join(import.meta.dirname, "..", "migrations"),
      runsRoot: join(root, "runs"),
      knowledgeRoot: join(root, "knowledge"),
      providerType: "deterministic",
    });
    const server = new InteractiveServer({ runManager: manager, port: 0 });
    let socket: BufferedWebSocket | null = null;

    try {
      await server.start();
      socket = await openBufferedWebSocket(server.url);
      const hello = await socket.waitFor((message) => message.type === "hello");
      expect(hello).toEqual({
        type: "hello",
        protocol_version: 2,
        capabilities: ["structured_task_creation_v1", "agent_task_outcome_v1"],
      });
      await socket.waitFor((message) => message.type === "state");

      socket.send({
        type: "create_task",
        contract: {
          target: "Current report",
          deliverable: { description: "A revised report" },
          criteria: "Evaluate grounding.",
        },
        source_contents: [],
      });
      const validationGenerating = await socket.waitFor(
        (message) =>
          message.type === "scenario_generating" && message.name === "improvement_task",
      );
      const validationError = await socket.waitFor(
        (message) => message.type === "scenario_error",
      );
      expect(validationError).toMatchObject({ stage: "validation" });
      expect(String(validationError.message)).toContain("contract.objective");
      expect(String(validationError.message).length).toBeLessThanOrEqual(1_024);
      expect(socket.messages.indexOf(validationGenerating)).toBeLessThan(
        socket.messages.indexOf(validationError),
      );

      socket.send({
        type: "create_task",
        contract: {
          objective: "Improve the current report.",
          target: "Current report",
          deliverable: { description: "A revised report" },
          criteria: "Evaluate grounding.",
          iterations: 2,
        },
        source_contents: [],
      });
      await expect(
        socket.waitFor((message) => message.type === "scenario_preview"),
      ).resolves.toMatchObject({ name: "improve_the_current_report" });
    } finally {
      socket?.close();
      await server.stop();
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("releases an unconfirmed task draft when its client disconnects", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-structured-disconnect-"));
    const manager = new RunManager({
      dbPath: join(root, "autocontext.db"),
      migrationsDir: join(import.meta.dirname, "..", "migrations"),
      runsRoot: join(root, "runs"),
      knowledgeRoot: join(root, "knowledge"),
      providerType: "deterministic",
    });
    const server = new InteractiveServer({ runManager: manager, port: 0 });
    let firstClient: BufferedWebSocket | null = null;
    let secondClient: BufferedWebSocket | null = null;
    const task = {
      type: "create_task",
      contract: {
        objective: "Improve a disconnected draft.",
        target: "Current draft",
        deliverable: { description: "A revised draft" },
        criteria: "Evaluate grounding.",
        iterations: 2,
      },
      source_contents: [],
    };

    try {
      await server.start();
      firstClient = await openBufferedWebSocket(server.url);
      await firstClient.waitFor((message) => message.type === "state");
      firstClient.send(task);
      const abandoned = await firstClient.waitFor(
        (message) => message.type === "scenario_preview",
      );
      firstClient.close();
      await firstClient.waitForClose();

      secondClient = await openBufferedWebSocket(server.url);
      await secondClient.waitFor((message) => message.type === "state");
      secondClient.send(task);
      await expect(
        secondClient.waitFor((message) => message.type === "scenario_preview"),
      ).resolves.toMatchObject({ name: abandoned.name });
    } finally {
      firstClient?.close();
      secondClient?.close();
      await server.stop();
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("isolates same-objective task drafts and confirmations across live clients", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-structured-scopes-"));
    const knowledgeRoot = join(root, "knowledge");
    const manager = new RunManager({
      dbPath: join(root, "autocontext.db"),
      migrationsDir: join(import.meta.dirname, "..", "migrations"),
      runsRoot: join(root, "runs"),
      knowledgeRoot,
      providerType: "deterministic",
    });
    const server = new InteractiveServer({ runManager: manager, port: 0 });
    let clientA: BufferedWebSocket | null = null;
    let clientB: BufferedWebSocket | null = null;
    const taskMessage = (id: string, content: string): WireMessage => ({
      type: "create_task",
      contract: {
        objective: "Improve the shared report.",
        target: "Current report",
        deliverable: { description: "A grounded revised report" },
        dataSources: [
          sourceDescriptor({
            id,
            role: "target",
            name: `${id}.txt`,
            contentRef: `mission://${id}`,
            content,
          }),
        ],
        criteria: "Use only the supplied evidence.",
        iterations: 2,
      },
      source_contents: [{ sourceId: id, content }],
    });

    try {
      await server.start();
      clientA = await openBufferedWebSocket(server.url);
      clientB = await openBufferedWebSocket(server.url);
      await Promise.all([
        clientA.waitFor((message) => message.type === "state"),
        clientB.waitFor((message) => message.type === "state"),
      ]);

      clientA.send(taskMessage("source-a", "CLIENT_A_ONLY"));
      const previewA = await clientA.waitFor((message) => message.type === "scenario_preview");

      clientB.send({ type: "confirm_scenario" });
      await expect(
        clientB.waitFor((message) => message.type === "scenario_error"),
      ).resolves.toMatchObject({
        stage: "server",
        message: "No scenario preview is pending. Create a scenario first.",
      });

      clientB.send(taskMessage("source-b", "CLIENT_B_ONLY"));
      const previewB = await clientB.waitFor((message) => message.type === "scenario_preview");
      expect(previewB.name).not.toBe(previewA.name);

      clientA.send({ type: "confirm_scenario" });
      const readyA = await clientA.waitFor((message) => message.type === "scenario_ready");
      expect(readyA.name).toBe(previewA.name);

      clientB.send({ type: "confirm_scenario" });
      const readyB = await clientB.waitFor((message) => message.type === "scenario_ready");
      expect(readyB.name).toBe(previewB.name);

      const persistedA = JSON.parse(
        readFileSync(
          join(
            knowledgeRoot,
            "_custom_scenarios",
            String(readyA.name),
            "agent_task_spec.json",
          ),
          "utf8",
        ),
      ) as Record<string, unknown>;
      const persistedB = JSON.parse(
        readFileSync(
          join(
            knowledgeRoot,
            "_custom_scenarios",
            String(readyB.name),
            "agent_task_spec.json",
          ),
          "utf8",
        ),
      ) as Record<string, unknown>;
      expect(persistedA.sample_input).toContain("CLIENT_A_ONLY");
      expect(persistedA.sample_input).not.toContain("CLIENT_B_ONLY");
      expect(persistedB.sample_input).toContain("CLIENT_B_ONLY");
      expect(persistedB.sample_input).not.toContain("CLIENT_A_ONLY");
    } finally {
      clientA?.close();
      clientB?.close();
      await server.stop();
      rmSync(root, { recursive: true, force: true });
    }
  });
});
