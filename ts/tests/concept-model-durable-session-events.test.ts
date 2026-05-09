import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const repoRoot = join(import.meta.dirname, "..", "..");

function readConceptDoc(): string {
  return readFileSync(join(repoRoot, "docs", "concept-model.md"), "utf-8");
}

function readConceptModel(): {
  mappings: Array<{
    surface: string;
    canonical_concept: string;
    category: string;
    notes?: string;
  }>;
} {
  return JSON.parse(
    readFileSync(join(repoRoot, "docs", "concept-model.json"), "utf-8"),
  ) as {
    mappings: Array<{
      surface: string;
      canonical_concept: string;
      category: string;
      notes?: string;
    }>;
  };
}

function durableSessionSection(): string {
  const match = readConceptDoc().match(
    /## Durable Session Event Storage\n([\s\S]*?)(?=\n## |\n$)/,
  );
  expect(match).not.toBeNull();
  return match?.[1] ?? "";
}

describe("durable session event storage concept model", () => {
  it("maps runtime-session events to canonical runtime vocabulary", () => {
    const section = durableSessionSection();

    for (const term of [
      "runtime-session event log",
      "Run",
      "Step",
      "Artifact",
      "Knowledge",
      "Budget",
      "Policy",
      "PROMPT_SUBMITTED",
      "ASSISTANT_MESSAGE",
      "SHELL_COMMAND",
      "TOOL_CALL",
      "CHILD_TASK_STARTED",
      "CHILD_TASK_COMPLETED",
      "COMPACTION",
    ]) {
      expect(section).toContain(term);
    }
  });

  it("keeps child lineage, replay, and compaction requirements explicit", () => {
    const section = durableSessionSection();

    for (const term of [
      "RuntimeSessionEventLog",
      "RuntimeSessionEventStore",
      "parentSessionId",
      "childSessionId",
      "taskId",
      "workerId",
      "eventId",
      "sequence",
      "replay",
      "compaction",
      "RunTrace",
      "production trace",
    ]) {
      expect(section).toContain(term);
    }
  });

  it("exposes runtime-session logs as artifact mappings, not a new top-level noun", () => {
    const mapping = readConceptModel().mappings.find(
      (candidate) => candidate.surface === "runtime-session event log",
    );

    expect(mapping).toEqual(
      expect.objectContaining({
        canonical_concept: "Artifact",
        category: "artifact",
      }),
    );
    expect(mapping?.notes).toContain("Run");
    expect(mapping?.notes).toContain("child task");
    expect(mapping?.notes).toContain("Knowledge");
  });
});
