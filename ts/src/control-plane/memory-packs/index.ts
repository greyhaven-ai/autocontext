import type { ValidationResult } from "../contract/types.js";

export type OperationalMemoryPackStatus = "draft" | "sanitized" | "active" | "deprecated";
export type OperationalMemoryRisk = "low" | "medium" | "high";

export interface OperationalMemoryFinding {
  readonly id: string;
  readonly summary: string;
  readonly evidenceRefs: readonly string[];
  readonly reusableBehavior: string;
  readonly targetFamilies: readonly string[];
  readonly risk: OperationalMemoryRisk;
  readonly containsTaskAnswer?: boolean;
  readonly containsSecret?: boolean;
}

export interface OperationalMemoryPack {
  readonly packId: string;
  readonly version: string;
  readonly createdAt: string;
  readonly status: OperationalMemoryPackStatus;
  readonly findings: readonly OperationalMemoryFinding[];
}

export function validateOperationalMemoryPack(input: unknown): ValidationResult {
  const errors: string[] = [];

  if (!isRecord(input)) {
    return { valid: false, errors: ["memory pack must be an object"] };
  }

  requireString(input, "packId", errors);
  requireString(input, "version", errors);
  requireString(input, "createdAt", errors);
  requireEnum(input, "status", ["draft", "sanitized", "active", "deprecated"], errors);

  if (!Array.isArray(input.findings)) {
    errors.push("findings must be an array");
  } else {
    for (const finding of input.findings) {
      validateFinding(finding, errors);
    }
  }

  return errors.length === 0 ? { valid: true } : { valid: false, errors };
}

function validateFinding(input: unknown, errors: string[]): void {
  if (!isRecord(input)) {
    errors.push("finding must be an object");
    return;
  }

  const id = typeof input.id === "string" && input.id.length > 0 ? input.id : "<unknown>";
  requireString(input, "id", errors);
  requireString(input, "summary", errors);
  requireString(input, "reusableBehavior", errors);
  requireStringArray(input, "evidenceRefs", errors);
  requireStringArray(input, "targetFamilies", errors);
  requireEnum(input, "risk", ["low", "medium", "high"], errors);
  requireOptionalBoolean(input, "containsTaskAnswer", id, errors);
  requireOptionalBoolean(input, "containsSecret", id, errors);

  if (input.containsTaskAnswer === true) {
    errors.push(`finding ${id} contains task-specific answer material`);
  }
  if (input.containsSecret === true) {
    errors.push(`finding ${id} contains secret material`);
  }
}

function requireOptionalBoolean(
  input: Readonly<Record<string, unknown>>,
  field: "containsTaskAnswer" | "containsSecret",
  findingId: string,
  errors: string[],
): void {
  if (Object.prototype.hasOwnProperty.call(input, field) && typeof input[field] !== "boolean") {
    errors.push(`finding ${findingId} ${field} must be a boolean when present`);
  }
}

function requireString(
  input: Readonly<Record<string, unknown>>,
  field: string,
  errors: string[],
): void {
  if (typeof input[field] !== "string" || input[field].length === 0) {
    errors.push(`${field} must be a non-empty string`);
  }
}

function requireStringArray(
  input: Readonly<Record<string, unknown>>,
  field: string,
  errors: string[],
): void {
  const value = input[field];
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string" && item.length > 0)) {
    errors.push(`${field} must be an array of non-empty strings`);
  }
}

function requireEnum(
  input: Readonly<Record<string, unknown>>,
  field: string,
  values: readonly string[],
  errors: string[],
): void {
  if (typeof input[field] !== "string" || !values.includes(input[field])) {
    errors.push(`${field} must be one of ${values.join(", ")}`);
  }
}

function isRecord(input: unknown): input is Readonly<Record<string, unknown>> {
  return typeof input === "object" && input !== null && !Array.isArray(input);
}
