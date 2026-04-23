import { parseDelimitedJsonObject } from "./llm-json-response.js";
import { healSpec } from "./spec-auto-heal.js";

export interface FamilyDesignerDescriptor<TSpec> {
  family: string;
  startDelimiter: string;
  endDelimiter: string;
  missingDelimiterLabel: string;
  parseRaw: (raw: Record<string, unknown>) => TSpec;
}

export function parseFamilyDesignerSpec<TSpec>(
  text: string,
  descriptor: FamilyDesignerDescriptor<TSpec>,
): TSpec {
  const raw = parseDelimitedJsonObject({
    text,
    startDelimiter: descriptor.startDelimiter,
    endDelimiter: descriptor.endDelimiter,
    missingDelimiterLabel: descriptor.missingDelimiterLabel,
  });
  return descriptor.parseRaw(healSpec(raw, descriptor.family));
}

export async function designFamilySpec<TSpec>(
  description: string,
  systemPrompt: string,
  descriptor: FamilyDesignerDescriptor<TSpec>,
  llmFn: (system: string, user: string) => Promise<string>,
): Promise<TSpec> {
  return parseFamilyDesignerSpec(
    await llmFn(systemPrompt, `User description:\n${description}`),
    descriptor,
  );
}
