import { describe, test, expect } from "vitest";
import {
  CURRENT_SCHEMA_VERSION,
  parseSchemaVersion,
  isReadCompatible,
  canWriteVersion,
  compareSchemaVersions,
} from "../../../src/control-plane/contract/schema-version.js";

describe("CURRENT_SCHEMA_VERSION", () => {
  test("is 1.0", () => {
    expect(CURRENT_SCHEMA_VERSION).toBe("1.0");
  });
});

describe("parseSchemaVersion", () => {
  test("accepts valid MAJOR.MINOR strings", () => {
    expect(parseSchemaVersion("1.0")).toBe("1.0");
    expect(parseSchemaVersion("2.5")).toBe("2.5");
    expect(parseSchemaVersion("10.99")).toBe("10.99");
  });

  test("rejects malformed versions", () => {
    expect(parseSchemaVersion("")).toBeNull();
    expect(parseSchemaVersion("1")).toBeNull();          // missing minor
    expect(parseSchemaVersion("1.0.0")).toBeNull();      // three components
    expect(parseSchemaVersion("v1.0")).toBeNull();       // leading v
    expect(parseSchemaVersion("1.a")).toBeNull();        // non-numeric
    expect(parseSchemaVersion(" 1.0 ")).toBeNull();      // whitespace
    expect(parseSchemaVersion("01.0")).toBeNull();       // leading zero
  });
});

describe("compareSchemaVersions", () => {
  test("compares major first then minor", () => {
    expect(compareSchemaVersions("1.0", "1.0")).toBe(0);
    expect(compareSchemaVersions("1.0", "1.1")).toBeLessThan(0);
    expect(compareSchemaVersions("1.2", "1.1")).toBeGreaterThan(0);
    expect(compareSchemaVersions("1.99", "2.0")).toBeLessThan(0);
    expect(compareSchemaVersions("10.0", "2.99")).toBeGreaterThan(0);
  });
});

describe("isReadCompatible (semver-lite: same MAJOR)", () => {
  test("true when MAJOR matches regardless of MINOR direction", () => {
    expect(isReadCompatible("1.0", "1.0")).toBe(true);
    expect(isReadCompatible("1.0", "1.5")).toBe(true);
    expect(isReadCompatible("1.5", "1.0")).toBe(true);
  });

  test("false when MAJOR differs", () => {
    expect(isReadCompatible("1.0", "2.0")).toBe(false);
    expect(isReadCompatible("2.0", "1.0")).toBe(false);
  });
});

describe("canWriteVersion (tools must not downgrade)", () => {
  test("consumer can write a version >= declared repo version", () => {
    expect(canWriteVersion("1.0", "1.0")).toBe(true);
    expect(canWriteVersion("1.1", "1.0")).toBe(true);  // writing 1.0 is OK when 1.1 consumer
  });

  test("consumer CANNOT write a lower version than declared", () => {
    // If repo declares 1.5 in VERSION, consumer using 1.0 must refuse to write
    expect(canWriteVersion("1.0", "1.5")).toBe(false);
    expect(canWriteVersion("1.0", "2.0")).toBe(false);
  });
});
