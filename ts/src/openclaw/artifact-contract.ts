import { assertSafeScenarioId } from "../knowledge/scenario-id.js";

export type OpenClawArtifactType = "harness" | "policy" | "distilled_model";

export interface ValidatedOpenClawArtifact {
  artifactId: string;
  artifactType: OpenClawArtifactType;
  scenario: string;
  data: Record<string, unknown>;
}

const SAFE_FILE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

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
  return value.map((entry) => entry.trim()).filter(Boolean);
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
  if (!SAFE_FILE_ID.test(artifactId)) {
    throw new Error(`invalid artifact id: ${artifactId}`);
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
  const data: Record<string, unknown> = {
    ...body,
    id: artifactId,
    name: requireString(body, "name"),
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
    data.source_code = requireSourceText(body, "source_code");
  } else {
    data.architecture = requireString(body, "architecture");
    data.parameter_count = requireInteger(body, "parameter_count", 1);
    data.checkpoint_path = requireString(body, "checkpoint_path");
    data.training_data_stats = optionalRecord(body, "training_data_stats");
  }

  return { artifactId, artifactType, scenario, data };
}
