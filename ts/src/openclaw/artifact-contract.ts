import { assertSafeScenarioId } from "../knowledge/scenario-id.js";

export type OpenClawArtifactType = "harness" | "policy" | "distilled_model";

export interface ValidatedOpenClawArtifact {
  artifactId: string;
  artifactType: OpenClawArtifactType;
  scenario: string;
  data: Record<string, unknown>;
}

// Keep this grammar identical to Python's portable OpenClaw artifact models.
const SAFE_FILE_ID = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;
const LEGACY_SAFE_FILE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const MAX_ARTIFACT_ID_CHARS = 128;
const MAX_SCENARIO_ID_CHARS = 128;
const MAX_NAME_CHARS = 512;
const MAX_SOURCE_BYTES = 1024 * 1024;
const MAX_LIST_ITEMS = 1_000;
const MAX_LIST_ITEM_CHARS = 512;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function requireString(body: Record<string, unknown>, key: string): string {
  const value = body[key];
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${key} is required`);
  }
  return value.trim();
}

function requireSourceText(body: Record<string, unknown>, key: string): string {
  const value = body[key];
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${key} is required`);
  }
  return value;
}

function requireInteger(body: Record<string, unknown>, key: string, min: number): number {
  const value = body[key];
  if (typeof value !== "number" || !Number.isInteger(value) || value < min) {
    throw new Error(`${key} must be an integer greater than or equal to ${min}`);
  }
  return value;
}

function optionalStringList(body: Record<string, unknown>, key: string): string[] {
  const value = body[key];
  if (value === undefined) {
    return [];
  }
  if (!Array.isArray(value) || !value.every((entry) => typeof entry === "string")) {
    throw new Error(`${key} must be a list of strings`);
  }
  if (value.length > MAX_LIST_ITEMS) {
    throw new Error(`${key} exceeds ${MAX_LIST_ITEMS} item limit`);
  }
  const normalized = value.map((entry) => entry.trim()).filter(Boolean);
  if (normalized.some((entry) => entry.length > MAX_LIST_ITEM_CHARS)) {
    throw new Error(`${key} entries exceed ${MAX_LIST_ITEM_CHARS} character limit`);
  }
  return normalized;
}

function optionalRecord(body: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = body[key];
  if (value === undefined) {
    return {};
  }
  if (!isRecord(value)) {
    throw new Error(`${key} must be an object`);
  }
  return value;
}

function isOpenClawArtifactType(value: string): value is OpenClawArtifactType {
  return value === "harness" || value === "policy" || value === "distilled_model";
}

function validateProvenance(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new Error("provenance is required");
  }

  return {
    ...value,
    run_id: requireString(value, "run_id"),
    generation: requireInteger(value, "generation", 0),
    scenario: requireString(value, "scenario"),
    settings: optionalRecord(value, "settings"),
  };
}

export function ensureSafeArtifactId(artifactId: string): string {
  if (artifactId.length > MAX_ARTIFACT_ID_CHARS || !SAFE_FILE_ID.test(artifactId)) {
    throw new Error(`invalid artifact id: ${artifactId}`);
  }
  return artifactId;
}

/**
 * Validate an identifier used only to read data written by older TypeScript
 * releases. Dots remain storage-safe but are intentionally forbidden for all
 * new portable artifact writes.
 */
export function ensureSafeLegacyArtifactReadId(artifactId: string): string {
  if (artifactId.length > MAX_ARTIFACT_ID_CHARS || !LEGACY_SAFE_FILE_ID.test(artifactId)) {
    throw new Error(`invalid legacy artifact id: ${artifactId}`);
  }
  return artifactId;
}

export function validateOpenClawArtifactPayload(body: Record<string, unknown>): ValidatedOpenClawArtifact {
  const rawArtifactType = requireString(body, "artifact_type");
  if (!isOpenClawArtifactType(rawArtifactType)) {
    throw new Error(
      `Invalid or missing artifact_type: ${rawArtifactType}. Must be harness, policy, or distilled_model.`,
    );
  }
  const artifactType = rawArtifactType;
  const artifactId = ensureSafeArtifactId(requireString(body, "id"));
  const scenario = assertSafeScenarioId(requireString(body, "scenario"));
  if (scenario.length > MAX_SCENARIO_ID_CHARS) {
    throw new Error(`scenario exceeds ${MAX_SCENARIO_ID_CHARS} character limit`);
  }
  const name = requireString(body, "name");
  if (name.length > MAX_NAME_CHARS) {
    throw new Error(`name exceeds ${MAX_NAME_CHARS} character limit`);
  }
  const data: Record<string, unknown> = {
    ...body,
    id: artifactId,
    name,
    artifact_type: artifactType,
    scenario,
    version: requireInteger(body, "version", 1),
    provenance: validateProvenance(body.provenance),
    created_at: typeof body.created_at === "string" && body.created_at.trim()
      ? body.created_at.trim()
      : new Date().toISOString(),
    compatible_scenarios: optionalStringList(body, "compatible_scenarios"),
    tags: optionalStringList(body, "tags"),
  };

  if (artifactType === "harness" || artifactType === "policy") {
    const sourceCode = requireSourceText(body, "source_code");
    if (Buffer.byteLength(sourceCode, "utf-8") > MAX_SOURCE_BYTES) {
      throw new Error(`source_code exceeds ${MAX_SOURCE_BYTES} byte limit`);
    }
    data.source_code = sourceCode;
  } else {
    data.architecture = requireString(body, "architecture");
    data.parameter_count = requireInteger(body, "parameter_count", 1);
    data.checkpoint_path = requireString(body, "checkpoint_path");
    data.training_data_stats = optionalRecord(body, "training_data_stats");
  }

  return { artifactId, artifactType, scenario, data };
}
