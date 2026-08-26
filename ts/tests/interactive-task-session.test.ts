import { Buffer } from "node:buffer";
import { createHash } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { resolveCustomAgentTask } from "../src/scenarios/custom-loader.js";
import type { ScenarioDraft } from "../src/scenarios/draft-workflow.js";
import type { CreatedScenarioResult } from "../src/scenarios/scenario-creator.js";
import type { TaskDataSource } from "../src/scenarios/task-data-source.js";
import { InteractiveScenarioSession } from "../src/server/interactive-scenario-session.js";

describe("interactive structured task session", () => {
  it("previews and persists a native agent task with role-routed source content", async () => {
    const inputContent = "incident_id,severity\n1,high";
    const inputBytes = Buffer.from(inputContent, "utf8");
    const persistInteractiveScenarioDraft = vi.fn(
      async (_opts: { draft: ScenarioDraft; knowledgeRoot: string }) => ({
        persisted: true,
        generatedSource: false,
        scenarioDir: "/tmp/knowledge/_custom_scenarios/improve_incident_triage",
        family: "agent_task",
        name: "improve_incident_triage",
        errors: [],
      }),
    );
    const session = new InteractiveScenarioSession({
      knowledgeRoot: "/tmp/knowledge",
      humanizeName: (name) => name.replaceAll("_", " "),
      deps: { persistInteractiveScenarioDraft },
    });

    const preview = await session.createTask({
      contract: {
        schemaVersion: 1,
        objective: "Improve incident triage summaries.",
        target: "The current incident triage summary.",
        deliverable: {
          description: "A concise, actionable incident summary.",
          outputFormat: "free_text",
        },
        dataSources: [
          {
            schemaVersion: 1,
            id: "triage-input",
            role: "input",
            name: "incidents.csv",
            contentRef: "autowork://mission-data/triage-input",
            mediaType: "text/csv",
            provenance: { origin: "autowork_upload", metadata: {} },
            integrity: {
              contentHash: `sha256:${createHash("sha256").update(inputBytes).digest("hex")}`,
              byteLength: inputBytes.byteLength,
              truncated: false,
            },
          },
        ],
        criteria: "Evaluate accuracy, completeness, and actionability.",
        qualityThreshold: 0.85,
        iterations: 3,
        revisionPrompt: "Use the evaluation feedback to improve the summary.",
      },
      sourceContents: [{ sourceId: "triage-input", content: inputContent }],
    });

    expect(preview.description).toContain("concise, actionable incident summary");
    expect(preview.winThreshold).toBe(0.85);
    expect(preview.constraints).toContain("Improvement loop: up to 3 attempts.");

    await expect(session.confirmScenario()).resolves.toEqual({
      name: preview.name,
      testScores: [],
    });
    const persistedDraft = persistInteractiveScenarioDraft.mock.calls[0]?.[0].draft;
    if (!persistedDraft) throw new Error("Expected the structured task draft to persist");
    expect(persistedDraft.preview.spec).toMatchObject({
      maxRounds: 3,
      qualityThreshold: 0.85,
      referenceSources: ["autowork://mission-data/triage-input"],
      taskDataSources: [
        expect.objectContaining({
          id: "triage-input",
          role: "input",
          provenance: expect.objectContaining({ origin: "autowork_upload" }),
        }),
      ],
    });
    expect(persistedDraft.preview.spec.sampleInput).toContain("incident_id,severity");
    expect(persistedDraft.preview.spec.sampleInput).toContain(
      "[BEGIN UNTRUSTED TASK DATA: input: incidents.csv",
    );
  });

  it("rejects in-place refinement of a structured task without mutating its data boundary", async () => {
    const referenceContent = "Visible product requirements";
    const evalContent = "EVAL_SECRET: reject unsupported claims";
    const source = (id: string, role: "reference" | "eval", content: string): TaskDataSource => {
      const bytes = Buffer.from(content, "utf8");
      return {
        schemaVersion: 1,
        id,
        role,
        name: `${id}.txt`,
        contentRef: `autowork://mission-data/${id}`,
        mediaType: "text/plain",
        provenance: { origin: "autowork_upload", metadata: {} },
        integrity: {
          contentHash: `sha256:${createHash("sha256").update(bytes).digest("hex")}`,
          byteLength: bytes.byteLength,
          truncated: false,
        },
      };
    };
    const reviseSpec = vi.fn(async (opts: { currentSpec: Record<string, unknown> }) => ({
      changesApplied: true,
      original: opts.currentSpec,
      revised: {
        ...opts.currentSpec,
        taskPrompt: "Write a more precise product assessment.",
        evaluationContext: "MUTATED_EVAL_SECRET",
        evaluation_context: "MUTATED_SNAKE_CASE_SECRET",
      },
    }));
    const session = new InteractiveScenarioSession({
      knowledgeRoot: "/tmp/knowledge",
      humanizeName: (name) => name.replaceAll("_", " "),
      deps: { reviseSpec },
    });

    const preview = await session.createTask({
      contract: {
        schemaVersion: 1,
        objective: "Improve the product assessment.",
        target: "The current product assessment.",
        deliverable: {
          description: "A grounded product assessment.",
          outputFormat: "free_text",
        },
        dataSources: [
          source("reference", "reference", referenceContent),
          source("eval", "eval", evalContent),
        ],
        criteria: "Evaluate evidence coverage and factual support.",
        iterations: 3,
        revisionPrompt: "Address the evaluator feedback.",
      },
      sourceContents: [
        { sourceId: "reference", content: referenceContent },
        { sourceId: "eval", content: evalContent },
      ],
    });

    expect(JSON.stringify(preview)).not.toContain("EVAL_SECRET");
    const originalSpec = structuredClone(session.pendingScenario!.preview.spec);
    expect(originalSpec.improvementTaskContractVersion).toBe(1);
    await expect(
      session.reviseScenario({
        feedback: "Make the task prompt more precise.",
        provider: {
          name: "unused",
          defaultModel: () => "unused",
          complete: vi.fn(),
        },
      }),
    ).rejects.toThrow(/Structured task contracts cannot be revised in place/);

    expect(reviseSpec).not.toHaveBeenCalled();
    expect(session.pendingScenario?.preview.spec).toEqual(originalSpec);
    expect(session.pendingScenario?.preview.spec.referenceContext).toContain(referenceContent);
    expect(session.pendingScenario?.preview.spec.evaluationContext).toContain(evalContent);
    expect(session.pendingScenario?.preview.spec).not.toHaveProperty("evaluation_context");
  });

  it("does not restore an asynchronous draft after its client scope is cancelled", async () => {
    let resolveCreation!: (created: CreatedScenarioResult) => void;
    const delayedCreation = new Promise<CreatedScenarioResult>((resolve) => {
      resolveCreation = resolve;
    });
    const scope = {};
    const session = new InteractiveScenarioSession({
      knowledgeRoot: "/tmp/knowledge",
      humanizeName: (name) => name.replaceAll("_", " "),
      deps: {
        createScenarioFromDescription: vi.fn(async () => delayedCreation),
      },
    });

    const creation = session.createScenario({
      description: "Improve a delayed report.",
      provider: {
        name: "unused",
        defaultModel: () => "unused",
        complete: vi.fn(),
      },
      scope,
    });
    session.cancelScenario(scope);
    resolveCreation({
      name: "improve_a_delayed_report",
      family: "agent_task",
      spec: {
        taskPrompt: "Improve a delayed report.",
        rubric: "Evaluate the report.",
        description: "A better report.",
      },
    });

    await expect(creation).rejects.toThrow("cancelled or superseded");
    await expect(session.confirmScenario(scope)).rejects.toThrow(
      "No scenario preview is pending",
    );
  });

  it("preserves the first same-name task and reloads a stable content-addressed successor", async () => {
    const knowledgeRoot = mkdtempSync(join(tmpdir(), "ac-structured-task-collision-"));
    const createSession = () =>
      new InteractiveScenarioSession({
        knowledgeRoot,
        humanizeName: (name) => name.replaceAll("_", " "),
      });
    const createTask = async (
      session: InteractiveScenarioSession,
      sourceId: string,
      content: string,
    ) => {
      const bytes = Buffer.from(content, "utf8");
      return session.createTask({
        contract: {
          schemaVersion: 1,
          objective: "Improve the product thesis with attached evidence.",
          target: "The current product thesis.",
          deliverable: {
            description: "A grounded, actionable product thesis.",
            outputFormat: "free_text",
          },
          dataSources: [
            {
              schemaVersion: 1,
              id: sourceId,
              role: "input",
              name: "evidence.csv",
              contentRef: `autowork://mission-data/${sourceId}`,
              mediaType: "text/csv",
              provenance: { origin: "autowork_upload", metadata: {} },
              integrity: {
                contentHash: `sha256:${createHash("sha256").update(bytes).digest("hex")}`,
                byteLength: bytes.byteLength,
                truncated: false,
              },
            },
          ],
          criteria: "Evaluate grounding and actionability.",
          iterations: 2,
        },
        sourceContents: [{ sourceId, content }],
      });
    };

    try {
      const firstSession = createSession();
      const firstPreview = await createTask(
        firstSession,
        "evidence-a",
        "issue_id,title\nAC-1,Preserve the original task",
      );
      expect(firstPreview.name).toBe("improve_the_product_thesis");
      await expect(firstSession.confirmScenario()).resolves.toEqual({
        name: firstPreview.name,
        testScores: [],
      });

      const secondSession = createSession();
      const secondPreview = await createTask(
        secondSession,
        "evidence-b",
        "issue_id,title\nAC-2,Retain a collision-safe successor",
      );
      expect(secondPreview.name).toMatch(/^improve_the_product_thesis_[0-9a-f]{12}$/);
      expect(secondPreview.name).not.toBe(firstPreview.name);
      await expect(secondSession.confirmScenario()).resolves.toEqual({
        name: secondPreview.name,
        testScores: [],
      });

      const firstReloaded = resolveCustomAgentTask(knowledgeRoot, firstPreview.name);
      const secondReloaded = resolveCustomAgentTask(knowledgeRoot, secondPreview.name);
      expect(firstReloaded?.spec.sampleInput).toContain("AC-1,Preserve the original task");
      expect(firstReloaded?.spec.sampleInput).not.toContain("AC-2");
      expect(secondReloaded?.spec.sampleInput).toContain("AC-2,Retain a collision-safe successor");
      expect(secondReloaded?.spec.sampleInput).not.toContain("AC-1");

      const repeatedSession = createSession();
      const repeatedPreview = await createTask(
        repeatedSession,
        "evidence-b",
        "issue_id,title\nAC-2,Retain a collision-safe successor",
      );
      expect(repeatedPreview.name).toBe(secondPreview.name);
    } finally {
      rmSync(knowledgeRoot, { recursive: true, force: true });
    }
  });
});
