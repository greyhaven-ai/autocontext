import { readFileSync } from "node:fs";

import type { ShareGPTRecord } from "./dataset-discovery-types.js";

export function ioPairToShareGPT(item: Record<string, unknown>): ShareGPTRecord {
  const prompt = String(item.input ?? item.prompt ?? item.question ?? "");
  const response = String(item.output ?? item.response ?? item.answer ?? "");

  return {
    conversations: [
      { from: "human", value: prompt },
      { from: "gpt", value: response },
    ],
    metadata: item.score != null ? { score: item.score } : undefined,
  };
}

export function adaptJsonlDataset(
  path: string,
  warnings: string[] = [],
): ShareGPTRecord[] {
  const content = readFileSync(path, "utf-8");
  const records: ShareGPTRecord[] = [];
  const lines = content.trim().split("\n");

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (!line.trim()) {
      continue;
    }

    try {
      const parsed = JSON.parse(line) as Record<string, unknown>;
      if (Array.isArray(parsed.conversations)) {
        records.push(parsed as unknown as ShareGPTRecord);
      } else if (parsed.input && parsed.output) {
        records.push(ioPairToShareGPT(parsed));
      }
    } catch (err) {
      warnings.push(`Line ${index + 1}: ${err instanceof Error ? err.message : "parse error"}`);
    }
  }

  return records;
}

export function adaptJsonDataset(path: string): ShareGPTRecord[] {
  const content = readFileSync(path, "utf-8");
  const parsed = JSON.parse(content);

  if (Array.isArray(parsed)) {
    return parsed
      .filter((item) => item && typeof item === "object")
      .map((item) => {
        if (item.conversations) {
          return item as ShareGPTRecord;
        }
        if (item.input != null || item.prompt != null) {
          return ioPairToShareGPT(item as Record<string, unknown>);
        }
        return null;
      })
      .filter(Boolean) as ShareGPTRecord[];
  }

  if (parsed.conversations) {
    return [parsed as ShareGPTRecord];
  }
  if (parsed.input || parsed.prompt) {
    return [ioPairToShareGPT(parsed as Record<string, unknown>)];
  }
  return [];
}
