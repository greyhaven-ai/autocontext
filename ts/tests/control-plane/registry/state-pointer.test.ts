import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  readdirSync,
  writeFileSync,
  mkdirSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import {
  writeStatePointer,
  readStatePointer,
  deleteStatePointer,
  listStatePointers,
  statePointerPath,
} from "../../../src/control-plane/registry/state-pointer.js";
import type {
  Scenario,
  EnvironmentTag,
  ArtifactId,
} from "../../../src/control-plane/contract/branded-ids.js";

const SCENARIO = "grid_ctf" as Scenario;
const ENV_TAG = "production" as EnvironmentTag;
const ARTIFACT_A = "01KPEYB3BRQWK2WSHK9E93N6NP" as ArtifactId;
const ARTIFACT_B = "01KPEYB3BRYCQ6J235VBR7WBY8" as ArtifactId;

describe("state-pointer", () => {
  let registryRoot: string;

  beforeEach(() => {
    registryRoot = mkdtempSync(join(tmpdir(), "autocontext-state-"));
  });

  afterEach(() => {
    rmSync(registryRoot, { recursive: true, force: true });
  });

  test("statePointerPath nests by scenario / actuatorType / environmentTag", () => {
    const p = statePointerPath(registryRoot, SCENARIO, "prompt-patch", ENV_TAG);
    expect(p).toBe(
      join(
        registryRoot,
        ".autocontext",
        "state",
        "active",
        "grid_ctf",
        "prompt-patch",
        "production.json",
      ),
    );
  });

  test("writeStatePointer round-trips through readStatePointer", () => {
    writeStatePointer(registryRoot, SCENARIO, "prompt-patch", ENV_TAG, {
      artifactId: ARTIFACT_A,
      asOf: "2026-04-17T12:00:00.000Z",
    });
    const back = readStatePointer(registryRoot, SCENARIO, "prompt-patch", ENV_TAG);
    expect(back).toEqual({
      artifactId: ARTIFACT_A,
      asOf: "2026-04-17T12:00:00.000Z",
    });
  });

  test("readStatePointer returns null when no pointer file exists", () => {
    expect(
      readStatePointer(registryRoot, SCENARIO, "prompt-patch", ENV_TAG),
    ).toBeNull();
  });

  test("writeStatePointer overwrites the existing pointer atomically", () => {
    writeStatePointer(registryRoot, SCENARIO, "prompt-patch", ENV_TAG, {
      artifactId: ARTIFACT_A,
      asOf: "2026-04-17T12:00:00.000Z",
    });
    writeStatePointer(registryRoot, SCENARIO, "prompt-patch", ENV_TAG, {
      artifactId: ARTIFACT_B,
      asOf: "2026-04-17T12:30:00.000Z",
    });
    const back = readStatePointer(registryRoot, SCENARIO, "prompt-patch", ENV_TAG);
    expect(back?.artifactId).toBe(ARTIFACT_B);
  });

  test("writeStatePointer uses tmp-file + rename (no leftover .tmp files)", () => {
    writeStatePointer(registryRoot, SCENARIO, "prompt-patch", ENV_TAG, {
      artifactId: ARTIFACT_A,
      asOf: "2026-04-17T12:00:00.000Z",
    });
    const dir = join(
      registryRoot,
      ".autocontext",
      "state",
      "active",
      "grid_ctf",
      "prompt-patch",
    );
    const entries = readdirSync(dir);
    expect(entries.filter((e) => e.endsWith(".tmp"))).toEqual([]);
  });

  test("readStatePointer rejects malformed JSON", () => {
    const path = statePointerPath(registryRoot, SCENARIO, "prompt-patch", ENV_TAG);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, "not-json");
    expect(() => readStatePointer(registryRoot, SCENARIO, "prompt-patch", ENV_TAG)).toThrow();
  });

  test("readStatePointer rejects pointer missing required fields", () => {
    const path = statePointerPath(registryRoot, SCENARIO, "prompt-patch", ENV_TAG);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, JSON.stringify({ artifactId: ARTIFACT_A })); // no asOf
    expect(() => readStatePointer(registryRoot, SCENARIO, "prompt-patch", ENV_TAG)).toThrow(/asOf/);
  });

  test("deleteStatePointer removes the pointer if present", () => {
    writeStatePointer(registryRoot, SCENARIO, "prompt-patch", ENV_TAG, {
      artifactId: ARTIFACT_A,
      asOf: "2026-04-17T12:00:00.000Z",
    });
    deleteStatePointer(registryRoot, SCENARIO, "prompt-patch", ENV_TAG);
    expect(readStatePointer(registryRoot, SCENARIO, "prompt-patch", ENV_TAG)).toBeNull();
  });

  test("listStatePointers enumerates every (scenario, actuatorType, env) tuple", () => {
    writeStatePointer(registryRoot, SCENARIO, "prompt-patch", ENV_TAG, {
      artifactId: ARTIFACT_A,
      asOf: "2026-04-17T12:00:00.000Z",
    });
    writeStatePointer(registryRoot, "othello" as Scenario, "tool-policy", "staging" as EnvironmentTag, {
      artifactId: ARTIFACT_B,
      asOf: "2026-04-17T13:00:00.000Z",
    });
    const tuples = listStatePointers(registryRoot).map(
      (t) => `${t.scenario}|${t.actuatorType}|${t.environmentTag}|${t.pointer.artifactId}`,
    ).sort();
    expect(tuples).toEqual(
      [
        `grid_ctf|prompt-patch|production|${ARTIFACT_A}`,
        `othello|tool-policy|staging|${ARTIFACT_B}`,
      ].sort(),
    );
  });

  test("listStatePointers returns [] when state directory is absent", () => {
    expect(listStatePointers(registryRoot)).toEqual([]);
  });
});
