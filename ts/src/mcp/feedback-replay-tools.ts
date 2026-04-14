import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";

import type { SQLiteStore } from "../storage/index.js";

interface JsonToolResponse {
  content: Array<{
    type: "text";
    text: string;
  }>;
}

type McpToolRegistrar = {
  tool: (...args: any[]) => unknown;
};

interface FeedbackReplayInternals {
  readReplayArtifact(runsRoot: string, runId: string, generation: number): Record<string, unknown>;
}

const defaultInternals: FeedbackReplayInternals = {
  readReplayArtifact,
};

function jsonText(payload: unknown, indent?: number): JsonToolResponse {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(payload, null, indent),
      },
    ],
  };
}

export function readReplayArtifact(
  runsRoot: string,
  runId: string,
  generation: number,
): Record<string, unknown> {
  const replayDir = join(
    runsRoot,
    runId,
    "generations",
    `gen_${generation}`,
    "replays",
  );
  if (!existsSync(replayDir)) {
    return { error: `no replay directory for run=${runId} gen=${generation}` };
  }
  const replayFiles = readdirSync(replayDir)
    .filter((name) => name.endsWith(".json"))
    .sort();
  if (replayFiles.length === 0) {
    return { error: `no replay files under ${replayDir}` };
  }

  return parseReplayPayload(readFileSync(join(replayDir, replayFiles[0]), "utf-8"));
}

function parseReplayPayload(raw: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(raw);
  return isRecord(parsed) ? parsed : {};
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const RecordFeedbackArgsSchema = z.object({
  scenario: z.string(),
  agentOutput: z.string(),
  score: z.number().min(0).max(1).optional(),
  notes: z.string().default(""),
  generationId: z.string().optional(),
});
type RecordFeedbackArgs = z.infer<typeof RecordFeedbackArgsSchema>;

const GetFeedbackArgsSchema = z.object({
  scenario: z.string(),
  limit: z.number().int().default(10),
});
type GetFeedbackArgs = z.infer<typeof GetFeedbackArgsSchema>;

const RunReplayArgsSchema = z.object({
  runId: z.string(),
  generation: z.number().int(),
});
type RunReplayArgs = z.infer<typeof RunReplayArgsSchema>;

export function registerFeedbackReplayTools(
  server: McpToolRegistrar,
  opts: {
    store: Pick<SQLiteStore, "insertHumanFeedback" | "getHumanFeedback">;
    runsRoot: string;
    internals?: Partial<FeedbackReplayInternals>;
  },
): void {
  const internals: FeedbackReplayInternals = {
    ...defaultInternals,
    ...opts.internals,
  };

  server.tool(
    "record_feedback",
    "Record human feedback for a scenario evaluation",
    RecordFeedbackArgsSchema.shape,
    async (args: RecordFeedbackArgs) => {
      const feedbackId = opts.store.insertHumanFeedback(
        args.scenario,
        args.agentOutput,
        args.score ?? null,
        args.notes,
        args.generationId ?? null,
      );
      return jsonText({ feedbackId, scenario: args.scenario });
    },
  );

  server.tool(
    "get_feedback",
    "Retrieve human feedback for a scenario",
    GetFeedbackArgsSchema.shape,
    async (args: GetFeedbackArgs) => {
      const feedback = opts.store.getHumanFeedback(
        args.scenario,
        args.limit,
      );
      return jsonText(feedback, 2);
    },
  );

  server.tool(
    "run_replay",
    "Read replay JSON for a specific generation",
    RunReplayArgsSchema.shape,
    async (args: RunReplayArgs) => {
      const payload = internals.readReplayArtifact(
        opts.runsRoot,
        args.runId,
        args.generation,
      );
      return jsonText(payload, 2);
    },
  );
}
