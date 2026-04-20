import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";
import {
  registerDetectorPlugin,
  runInstrument,
  runInstrumentCommand,
  validateInstrumentPlan,
} from "../../../../src/index.js";

describe("instrument package surface", () => {
  test("root package exports the public instrument runtime API", () => {
    expect(registerDetectorPlugin).toBeTypeOf("function");
    expect(runInstrument).toBeTypeOf("function");
    expect(runInstrumentCommand).toBeTypeOf("function");
    expect(validateInstrumentPlan).toBeTypeOf("function");
  });

  test("package exports include the instrument subpath for plugin packages", () => {
    const pkg = JSON.parse(readFileSync(join(process.cwd(), "package.json"), "utf-8")) as {
      exports?: Record<string, unknown>;
    };
    expect(pkg.exports?.["./control-plane/instrument"]).toEqual({
      import: "./dist/control-plane/instrument/index.js",
      types: "./dist/control-plane/instrument/index.d.ts",
    });
  });
});
