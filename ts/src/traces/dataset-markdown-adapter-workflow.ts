import { readFileSync } from "node:fs";

import type { ShareGPTRecord } from "./dataset-discovery-types.js";

export function normalizeMarkdownHeading(heading: string): string {
  return heading
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

export function parseMarkdownSections(content: string): Map<string, string> {
  const sections = new Map<string, string>();
  const lines = content.split(/\r?\n/);
  let currentHeading: string | null = null;
  let buffer: string[] = [];

  const flush = () => {
    if (!currentHeading) {
      return;
    }
    const sectionBody = buffer.join("\n").trim();
    if (sectionBody) {
      sections.set(currentHeading, sectionBody);
    }
  };

  for (const line of lines) {
    const match = /^(#{1,6})\s+(.+?)\s*$/.exec(line);
    if (match) {
      flush();
      currentHeading = normalizeMarkdownHeading(match[2]);
      buffer = [];
      continue;
    }
    if (currentHeading) {
      buffer.push(line);
    }
  }

  flush();
  return sections;
}

export function findMarkdownSection(
  sections: Map<string, string>,
  candidates: string[],
): string | undefined {
  for (const candidate of candidates) {
    const normalizedCandidate = normalizeMarkdownHeading(candidate);
    for (const [heading, body] of sections.entries()) {
      if (
        heading === normalizedCandidate
        || heading.includes(normalizedCandidate)
        || normalizedCandidate.includes(heading)
      ) {
        return body;
      }
    }
  }
  return undefined;
}

export function adaptMarkdownDataset(path: string): ShareGPTRecord[] {
  const content = readFileSync(path, "utf-8");
  const sections = parseMarkdownSections(content);
  const prompt = findMarkdownSection(sections, [
    "input",
    "prompt",
    "question",
    "task",
    "instruction",
  ]);
  const response = findMarkdownSection(sections, [
    "expected output",
    "output",
    "response",
    "answer",
    "solution",
  ]);

  if (!prompt || !response) {
    return [];
  }

  return [{
    conversations: [
      { from: "human", value: prompt },
      { from: "gpt", value: response },
    ],
  }];
}
