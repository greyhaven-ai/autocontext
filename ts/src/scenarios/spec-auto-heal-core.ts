import { getStringValue } from "./spec-auto-heal-readers.js";

const NUMERIC_FIELD_PATTERNS =
  /^(max|min|limit|count|threshold|steps|rounds|quality|size|depth|width|height|port|timeout|retries)/i;
const BOOLEAN_FIELDS = new Set([
  "retryable",
  "enabled",
  "active",
  "visible",
  "required",
  "optional",
]);

export function coerceSpecTypes(
  spec: Record<string, unknown>,
): Record<string, unknown> {
  const result: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(spec)) {
    if (Array.isArray(value)) {
      result[key] = value.map((entry) =>
        entry != null && typeof entry === "object"
          ? coerceSpecTypes(entry as Record<string, unknown>)
          : entry,
      );
      continue;
    }

    if (value != null && typeof value === "object") {
      result[key] = coerceSpecTypes(value as Record<string, unknown>);
      continue;
    }

    if (typeof value === "string") {
      if (
        NUMERIC_FIELD_PATTERNS.test(key) ||
        key.endsWith("_steps") ||
        key.endsWith("Steps")
      ) {
        const num = Number(value);
        if (!isNaN(num) && value.trim() !== "") {
          result[key] = num;
          continue;
        }
      }

      if (BOOLEAN_FIELDS.has(key)) {
        if (value.toLowerCase() === "true") {
          result[key] = true;
          continue;
        }
        if (value.toLowerCase() === "false") {
          result[key] = false;
          continue;
        }
      }
    }

    result[key] = value;
  }

  return result;
}

export function inferMissingFields(
  spec: Record<string, unknown>,
): Record<string, unknown> {
  const result = { ...spec };
  const taskPrompt = getStringValue(spec, "taskPrompt", "task_prompt") ?? "";

  if (!getStringValue(result, "description")) {
    if (taskPrompt) {
      const firstSentence = taskPrompt.split(/[.!?]\s/)[0];
      result.description =
        firstSentence.length > 100
          ? firstSentence.slice(0, 100) + "..."
          : firstSentence + ".";
    }
  }

  const hasRubric = getStringValue(
    result,
    "rubric",
    "judgeRubric",
    "judge_rubric",
  );
  if (!hasRubric && taskPrompt) {
    const inferredRubric = `Evaluate the quality and completeness of the response to: ${taskPrompt.slice(0, 80)}`;
    result.rubric = inferredRubric;
    result.judgeRubric = inferredRubric;
    result.judge_rubric = inferredRubric;
  }

  return result;
}
