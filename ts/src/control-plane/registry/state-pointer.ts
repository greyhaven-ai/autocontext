import {
  mkdirSync,
  writeFileSync,
  readFileSync,
  existsSync,
  unlinkSync,
  renameSync,
  readdirSync,
  statSync,
} from "node:fs";
import { join, dirname } from "node:path";
import type {
  ArtifactId,
  EnvironmentTag,
  Scenario,
} from "../contract/branded-ids.js";
import type { ActuatorType } from "../contract/types.js";
import { canonicalJsonStringify } from "../contract/canonical-json.js";

export interface StatePointer {
  readonly artifactId: ArtifactId;
  readonly asOf: string;
}

export interface StatePointerEntry {
  readonly scenario: Scenario;
  readonly actuatorType: ActuatorType;
  readonly environmentTag: EnvironmentTag;
  readonly pointer: StatePointer;
}

const ROOT = ".autocontext";
const STATE = join("state", "active");

export function statePointerPath(
  registryRoot: string,
  scenario: Scenario,
  actuatorType: ActuatorType,
  environmentTag: EnvironmentTag,
): string {
  return join(
    registryRoot,
    ROOT,
    STATE,
    scenario,
    actuatorType,
    `${environmentTag}.json`,
  );
}

/**
 * Atomically write a state pointer for the given (scenario, actuatorType,
 * environmentTag) tuple. Uses tmp-file + rename so a crash mid-write leaves
 * the previous value intact.
 */
export function writeStatePointer(
  registryRoot: string,
  scenario: Scenario,
  actuatorType: ActuatorType,
  environmentTag: EnvironmentTag,
  pointer: StatePointer,
): void {
  const path = statePointerPath(registryRoot, scenario, actuatorType, environmentTag);
  mkdirSync(dirname(path), { recursive: true });
  const tmp = path + ".tmp";
  writeFileSync(tmp, canonicalJsonStringify(pointer), "utf-8");
  renameSync(tmp, path);
}

/**
 * Read a state pointer. Returns null when the file does not exist.
 * Throws if the file exists but is not valid JSON or is missing required fields.
 */
export function readStatePointer(
  registryRoot: string,
  scenario: Scenario,
  actuatorType: ActuatorType,
  environmentTag: EnvironmentTag,
): StatePointer | null {
  const path = statePointerPath(registryRoot, scenario, actuatorType, environmentTag);
  if (!existsSync(path)) return null;
  const raw = readFileSync(path, "utf-8");
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new Error(`readStatePointer: ${path} is not valid JSON: ${(err as Error).message}`);
  }
  return validatePointer(parsed, path);
}

function validatePointer(parsed: unknown, path: string): StatePointer {
  if (parsed === null || typeof parsed !== "object") {
    throw new Error(`readStatePointer: ${path} is not an object`);
  }
  const obj = parsed as Record<string, unknown>;
  if (typeof obj.artifactId !== "string") {
    throw new Error(`readStatePointer: ${path} missing or non-string artifactId`);
  }
  if (typeof obj.asOf !== "string") {
    throw new Error(`readStatePointer: ${path} missing or non-string asOf`);
  }
  return {
    artifactId: obj.artifactId as ArtifactId,
    asOf: obj.asOf,
  };
}

/**
 * Delete a state pointer. No-op if it doesn't exist.
 */
export function deleteStatePointer(
  registryRoot: string,
  scenario: Scenario,
  actuatorType: ActuatorType,
  environmentTag: EnvironmentTag,
): void {
  const path = statePointerPath(registryRoot, scenario, actuatorType, environmentTag);
  if (existsSync(path)) {
    unlinkSync(path);
  }
}

/**
 * Walk the entire `state/active/` tree and return one entry per pointer file.
 */
export function listStatePointers(registryRoot: string): StatePointerEntry[] {
  const root = join(registryRoot, ROOT, STATE);
  if (!existsSync(root)) return [];
  const out: StatePointerEntry[] = [];
  for (const scenario of readdirSync(root)) {
    const sDir = join(root, scenario);
    if (!isDir(sDir)) continue;
    for (const actuatorType of readdirSync(sDir)) {
      const aDir = join(sDir, actuatorType);
      if (!isDir(aDir)) continue;
      for (const fileName of readdirSync(aDir)) {
        if (!fileName.endsWith(".json")) continue;
        const envTag = fileName.slice(0, -".json".length);
        const fullPath = join(aDir, fileName);
        const raw = readFileSync(fullPath, "utf-8");
        const pointer = validatePointer(JSON.parse(raw), fullPath);
        out.push({
          scenario: scenario as Scenario,
          actuatorType: actuatorType as ActuatorType,
          environmentTag: envTag as EnvironmentTag,
          pointer,
        });
      }
    }
  }
  return out;
}

function isDir(p: string): boolean {
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
}
