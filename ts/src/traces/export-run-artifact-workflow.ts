import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import type { TraceMessage } from "./public-schema.js";

export function loadRunMessagesFromArtifacts(runDir: string): {
  messages: TraceMessage[];
  warnings: string[];
} {
  const messages: TraceMessage[] = [];
  const warnings: string[] = [];
  const timestamp = new Date().toISOString();

  const metaPath = join(runDir, "run_meta.json");
  if (existsSync(metaPath)) {
    try {
      const meta = JSON.parse(readFileSync(metaPath, "utf-8")) as {
        run_id?: string;
        scenario?: string;
        created_at?: string;
      };
      messages.push({
        role: "system",
        content: `Run ${meta.run_id} for scenario ${meta.scenario}`,
        timestamp: meta.created_at ?? timestamp,
      });
    } catch (error) {
      warnings.push(
        `Failed to parse ${metaPath}: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  const generationDir = join(runDir, "generations");
  if (!existsSync(generationDir)) {
    return { messages, warnings };
  }

  let generationEntries: string[];
  try {
    generationEntries = readdirSync(generationDir).sort();
  } catch (error) {
    warnings.push(
      `Failed to list generation artifacts in ${generationDir}: ${error instanceof Error ? error.message : String(error)}`,
    );
    return { messages, warnings };
  }

  const artifactFiles = [
    { file: "competitor_prompt.md", role: "user" as const },
    { file: "competitor_output.md", role: "assistant" as const },
    { file: "analyst.md", role: "assistant" as const },
    { file: "coach.md", role: "assistant" as const },
    { file: "trajectory.md", role: "system" as const },
  ];

  for (const generation of generationEntries) {
    const generationPath = join(generationDir, generation);
    for (const artifact of artifactFiles) {
      const filePath = join(generationPath, artifact.file);
      if (!existsSync(filePath)) {
        continue;
      }
      try {
        const content = readFileSync(filePath, "utf-8");
        if (content.trim()) {
          messages.push({ role: artifact.role, content, timestamp });
        }
      } catch (error) {
        warnings.push(
          `Failed to read ${filePath}: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
    }
  }

  return { messages, warnings };
}
