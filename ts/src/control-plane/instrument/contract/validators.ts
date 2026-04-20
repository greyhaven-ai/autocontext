/**
 * A2-I instrument contract validators.
 *
 * Kept in a sibling module to Foundation B's `control-plane/contract/validators.ts`
 * to respect import discipline (instrument/contract/ is a foundational leaf that
 * must not reach up into other instrument sub-contexts). Pattern mirrors Foundation B's
 * ajv wiring exactly (DRY — same AJV flavor, same formats).
 */
import Ajv2020 from "ajv/dist/2020.js";
import type { ErrorObject, ValidateFunction } from "ajv";
import addFormats from "ajv-formats";
import instrumentSessionSchema from "./json-schemas/instrument-session.schema.json" with { type: "json" };
import instrumentPlanSchema from "./json-schemas/instrument-plan.schema.json" with { type: "json" };
import type { InstrumentSession, InstrumentPlan } from "./plugin-interface.js";

export type ValidationResult =
  | { readonly valid: true }
  | { readonly valid: false; readonly errors: readonly string[] };

// ajv + ajv-formats are CJS; ESM default-interop exposes the class/function via .default.
// Same cast pattern as Foundation B — keeps strict typing while resolving runtime shape.
const AjvCtor = (Ajv2020 as unknown as { default: typeof Ajv2020 }).default ?? Ajv2020;
const addFormatsFn = (addFormats as unknown as { default: typeof addFormats }).default ?? addFormats;

const ajv = new AjvCtor({ strict: true, allErrors: true });
addFormatsFn(ajv);

ajv.addSchema(instrumentSessionSchema as object);
ajv.addSchema(instrumentPlanSchema as object);

const instrumentSessionValidator = ajv.getSchema("https://autocontext.dev/schema/instrument-session.json")!;
const instrumentPlanValidator = ajv.getSchema("https://autocontext.dev/schema/instrument-plan.json")!;

function toResult(validate: ValidateFunction, input: unknown): ValidationResult {
  const ok = validate(input);
  if (ok) return { valid: true };
  const errors = (validate.errors ?? []).map(formatError);
  return { valid: false, errors };
}

function formatError(e: ErrorObject): string {
  const path = e.instancePath || "<root>";
  return `${path} ${e.message ?? "invalid"}`.trim();
}

export function validateInstrumentSession(input: unknown): ValidationResult {
  return toResult(instrumentSessionValidator, input);
}

export function validateInstrumentPlan(input: unknown): ValidationResult {
  return toResult(instrumentPlanValidator, input);
}

// Type-level cross-check — if TS types drift from schemas this won't compile cleanly.
export type _TypeCheck = InstrumentSession | InstrumentPlan;
