import { readFileSync } from "node:fs";

import type { ShareGPTRecord } from "./dataset-discovery-types.js";

export function parseCSVLine(line: string): string[] {
  const values: string[] = [];
  let current = "";
  let inQuotes = false;
  let index = 0;

  while (index < line.length) {
    const char = line[index];
    if (char === '"') {
      if (inQuotes && index + 1 < line.length && line[index + 1] === '"') {
        current += '"';
        index += 2;
        continue;
      }
      inQuotes = !inQuotes;
    } else if (char === "," && !inQuotes) {
      values.push(current);
      current = "";
    } else {
      current += char;
    }
    index += 1;
  }

  values.push(current);
  return values;
}

export function adaptCsvDataset(path: string): ShareGPTRecord[] {
  const content = readFileSync(path, "utf-8");
  const lines = content.trim().split("\n");
  if (lines.length < 2) {
    return [];
  }

  const headers = parseCSVLine(lines[0]).map((header) => header.toLowerCase().trim());
  const promptColumn = headers.findIndex((header) => (
    header === "prompt" || header === "input" || header === "question"
  ));
  const responseColumn = headers.findIndex((header) => (
    header === "response" || header === "output" || header === "answer"
  ));
  if (promptColumn < 0 || responseColumn < 0) {
    return [];
  }

  const records: ShareGPTRecord[] = [];
  for (let index = 1; index < lines.length; index += 1) {
    const values = parseCSVLine(lines[index]);
    if (values.length <= Math.max(promptColumn, responseColumn)) {
      continue;
    }
    records.push({
      conversations: [
        { from: "human", value: values[promptColumn].trim() },
        { from: "gpt", value: values[responseColumn].trim() },
      ],
    });
  }

  return records;
}
