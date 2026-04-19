import { describe, test, expect } from "vitest";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const TS_ROOT = resolve(__dirname, "..", "..", "..", "..");
const GENERATED_PATH = resolve(TS_ROOT, "src/production-traces/contract/generated-types.ts");
const SCRIPT = resolve(TS_ROOT, "scripts/generate-production-traces-types.mjs");

describe("generated-types.ts drift check", () => {
  test("carries the AUTO-GENERATED banner", () => {
    const body = readFileSync(GENERATED_PATH, "utf-8");
    expect(body).toMatch(/AUTO-GENERATED/);
    expect(body).toMatch(/DO NOT EDIT/);
  });

  test("running generator in --check mode succeeds (no drift from schemas)", () => {
    const result = spawnSync("node", [SCRIPT, "--check"], {
      cwd: TS_ROOT,
      encoding: "utf-8",
      env: process.env,
    });
    if (result.status !== 0) {
      // eslint-disable-next-line no-console
      console.error("stdout:", result.stdout);
      // eslint-disable-next-line no-console
      console.error("stderr:", result.stderr);
    }
    expect(result.status).toBe(0);
  });

  test("generator output mentions the core aggregate interfaces", () => {
    const body = readFileSync(GENERATED_PATH, "utf-8");
    expect(body).toMatch(/export interface ProductionTrace/);
    expect(body).toMatch(/export interface TraceSource/);
    expect(body).toMatch(/export interface TraceMessage/);
    expect(body).toMatch(/export interface RedactionMarker/);
  });
});
