/**
 * Schema-enforced role outputs for the TypeScript engine (AC-929).
 *
 * Mirrors Python's autocontext/agents/role_schemas.py. The schemas themselves
 * are NOT retyped here: they are read from docs/role-output-schemas.json,
 * generated from the pydantic models. Retyping is precisely how the two engines
 * drift — during AC-913/AC-929 a hand-copied analyst schema was written twice,
 * once dropping the field descriptions (which ride to the model and shape what
 * it generates) and once dropping `minItems`, which is the whole difference
 * between "the key exists" and "the section has content".
 *
 * AJV validates responses against the same schema that was sent, so a payload
 * the backend accepted but that fails the contract raises rather than becoming
 * a silently empty section.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { Ajv, type ValidateFunction } from "ajv";

import type { OutputSchema } from "../types/index.js";
import type { AnalystOutput, ArchitectOutput, CoachOutput } from "./roles.js";

/**
 * Whether a backend actually enforced the requested schema.
 *
 * `CompletionResult.constrained` is `boolean | undefined` on purpose --
 * see the note on the type -- and "absent" and "false" both mean unconstrained.
 * Reading it through one helper keeps that comparison in a single place, so a
 * later `if (result.constrained)` cannot quietly treat undefined as a third
 * state, and callers never have to remember the `=== true`.
 */
export function wasConstrained(result: { constrained?: boolean }): boolean {
  return result.constrained === true;
}

/** A role's output did not conform to its schema. */
export class RoleOutputValidationError extends Error {
  readonly role: string;
  readonly reason: string;
  readonly rawText: string;

  constructor(role: string, reason: string, rawText: string) {
    super(`${role} output failed schema validation: ${reason}`);
    this.name = "RoleOutputValidationError";
    this.role = role;
    this.reason = reason;
    this.rawText = rawText;
  }
}

type SchemaArtifact = {
  contract: string;
  schemas: Record<string, Record<string, unknown>>;
};

function loadArtifact(): SchemaArtifact {
  // Resolve from this module rather than cwd. Builds copy the artifact into
  // dist/, while source execution falls back to the repository's docs/ tree.
  const here = dirname(fileURLToPath(import.meta.url));
  const candidates = [
    join(here, "..", "role-output-schemas.json"),
    join(here, "..", "..", "..", "docs", "role-output-schemas.json"),
  ];
  for (const path of candidates) {
    try {
      return JSON.parse(readFileSync(path, "utf-8")) as SchemaArtifact;
    } catch {
      continue;
    }
  }
  throw new Error(
    "role-output-schemas.json not found. Regenerate with " +
      "autocontext/scripts/generate_role_output_schemas.py",
  );
}

const ARTIFACT = loadArtifact();

function outputSchema(name: string): OutputSchema {
  const schema = ARTIFACT.schemas[name];
  if (!schema) {
    throw new Error(`role-output-schemas.json has no schema named ${name}`);
  }
  return { name, schema };
}

export const ANALYST_SCHEMA = outputSchema("analyst_output");
export const COACH_SCHEMA = outputSchema("coach_output");
export const ARCHITECT_SCHEMA = outputSchema("architect_output");

// strict:false — the generated schemas carry pydantic's `title`/`description`
// annotations, which AJV's strict mode rejects as unknown keywords. They are
// meaningful to the model, so they stay in the artifact rather than being
// stripped to satisfy the validator.
const ajv = new Ajv({ allErrors: true, strict: false });
const validators = new Map<string, ValidateFunction>();

function validatorFor(schema: OutputSchema): ValidateFunction {
  let validate = validators.get(schema.name);
  if (!validate) {
    validate = ajv.compile(schema.schema);
    validators.set(schema.name, validate);
  }
  return validate;
}

function parsePayload<T>(role: string, schema: OutputSchema, rawText: string): T {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch (err) {
    throw new RoleOutputValidationError(role, `not valid JSON: ${String(err)}`, rawText);
  }
  const validate = validatorFor(schema);
  if (!validate(parsed)) {
    const reason = (validate.errors ?? [])
      .map((e) => `${e.instancePath || "/"} ${e.message ?? ""}`.trim())
      .join("; ");
    throw new RoleOutputValidationError(role, reason || "did not match schema", rawText);
  }
  return parsed as T;
}

/** Render validated data into the markdown shape the existing scraper reads. */
export function renderAnalystMarkdown(payload: {
  findings: string[];
  root_causes: string[];
  recommendations: string[];
}): string {
  const sections: Array<[string, string[]]> = [
    ["Findings", payload.findings],
    ["Root Causes", payload.root_causes],
    ["Actionable Recommendations", payload.recommendations],
  ];
  return (
    sections
      .map(([heading, items]) =>
        items.length
          ? `## ${heading}\n\n${items.map((i) => `- ${i}`).join("\n")}`
          : `## ${heading}\n`,
      )
      .join("\n\n") + "\n"
  );
}

export function parseAnalystConstrained(rawText: string): AnalystOutput {
  const payload = parsePayload<{
    findings: string[];
    root_causes: string[];
    recommendations: string[];
  }>("analyst", ANALYST_SCHEMA, rawText);
  return {
    rawMarkdown: renderAnalystMarkdown(payload),
    findings: payload.findings,
    rootCauses: payload.root_causes,
    recommendations: payload.recommendations,
    parseSuccess: true,
  };
}

export function renderCoachMarkdown(payload: {
  playbook: string;
  lessons: string;
  hints: string;
}): string {
  return (
    `<!-- PLAYBOOK_START -->\n${payload.playbook}\n<!-- PLAYBOOK_END -->\n\n` +
    `<!-- LESSONS_START -->\n${payload.lessons}\n<!-- LESSONS_END -->\n\n` +
    `<!-- COMPETITOR_HINTS_START -->\n${payload.hints}\n<!-- COMPETITOR_HINTS_END -->\n`
  );
}

export function parseCoachConstrained(rawText: string): CoachOutput {
  const payload = parsePayload<{ playbook: string; lessons: string; hints: string }>(
    "coach",
    COACH_SCHEMA,
    rawText,
  );
  return {
    rawMarkdown: renderCoachMarkdown(payload),
    playbook: payload.playbook,
    lessons: payload.lessons,
    hints: payload.hints,
    parseSuccess: true,
  };
}

/**
 * NOT wired into the orchestrator. The Python payload carries nine channels and
 * a legacy-format renderer; this maps only tools and the changelog entry, so
 * wiring it would silently drop the rest. Exercised by tests so it cannot rot.
 *
 * AC-930 investigated finishing the port and concluded it is not worth doing on
 * the current shape: the only class that would consume the extra channels here
 * is `AgentOrchestrator`, which is referenced solely by tests and is not public
 * API -- production runs `loop/generation-runner.ts`. If the full payload is
 * ever needed on the production path, that is an issue against generation-runner,
 * not a port into this function.
 */
export function parseArchitectConstrained(rawText: string): ArchitectOutput {
  const payload = parsePayload<{
    tools: Array<{ name: string; description: string; code: string }>;
    changelog_entry: string;
  }>("architect", ARCHITECT_SCHEMA, rawText);
  return {
    rawMarkdown: rawText,
    toolSpecs: payload.tools.map((tool) => ({ ...tool })),
    // Dropped on purpose, and unlike the markdown path the data IS here:
    // ARCHITECT_SCHEMA declares a `harness` channel, so a constrained response
    // can carry validator specs that this line discards (AC-930). The specs are
    // Python source for the Python harness; ts/src has no consumer of them and
    // nothing that could execute one. Surfacing them would hand callers values
    // whose only correct use on this engine is to ignore them.
    harnessSpecs: [],
    changelogEntry: payload.changelog_entry,
    parseSuccess: true,
  };
}
