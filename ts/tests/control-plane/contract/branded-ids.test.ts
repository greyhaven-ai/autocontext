import { describe, test, expect } from "vitest";
import {
  newArtifactId,
  parseArtifactId,
  newChangeSetId,
  parseChangeSetId,
  parseScenario,
  parseEnvironmentTag,
  parseSuiteId,
  parseContentHash,
  defaultEnvironmentTag,
} from "../../../src/control-plane/contract/branded-ids.js";

describe("ArtifactId", () => {
  test("newArtifactId produces a valid ULID that round-trips through parseArtifactId", () => {
    const id = newArtifactId();
    expect(id).toHaveLength(26);
    const parsed = parseArtifactId(id as unknown as string);
    expect(parsed).toBe(id);
  });

  test("newArtifactId produces unique values across calls", () => {
    const a = newArtifactId();
    const b = newArtifactId();
    expect(a).not.toBe(b);
  });

  test("parseArtifactId rejects invalid input", () => {
    expect(parseArtifactId("")).toBeNull();
    expect(parseArtifactId("not-a-ulid")).toBeNull();
    expect(parseArtifactId("01J2XYZ7K8L9M0N1P2Q3R4")).toBeNull(); // 22 chars, too short
    expect(parseArtifactId("01J2XYZ7K8L9M0N1P2Q3R4S5T6X")).toBeNull(); // 27 chars, too long
    expect(parseArtifactId("01J2XYZ7K8L9M0N1P2Q3R4S5TI")).toBeNull(); // contains 'I' (Crockford excludes I/L/O/U)
  });
});

describe("ChangeSetId", () => {
  test("newChangeSetId produces a valid ULID", () => {
    const id = newChangeSetId();
    expect(id).toHaveLength(26);
    expect(parseChangeSetId(id as unknown as string)).toBe(id);
  });

  test("parseChangeSetId rejects invalid input", () => {
    expect(parseChangeSetId("")).toBeNull();
    expect(parseChangeSetId("not-a-ulid")).toBeNull();
  });
});

describe("Scenario", () => {
  test("parseScenario accepts non-empty strings matching the naming rule", () => {
    expect(parseScenario("grid_ctf")).toBe("grid_ctf");
    expect(parseScenario("othello")).toBe("othello");
    expect(parseScenario("my-scenario-v2")).toBe("my-scenario-v2");
  });

  test("parseScenario rejects empty string, whitespace, uppercase, or special chars", () => {
    expect(parseScenario("")).toBeNull();
    expect(parseScenario("   ")).toBeNull();
    expect(parseScenario("Grid_CTF")).toBeNull();      // uppercase not allowed
    expect(parseScenario("grid ctf")).toBeNull();      // space not allowed
    expect(parseScenario("grid.ctf")).toBeNull();      // dot not allowed
    expect(parseScenario("/grid_ctf")).toBeNull();     // path separator not allowed
  });
});

describe("EnvironmentTag", () => {
  test("parseEnvironmentTag accepts common tags", () => {
    expect(parseEnvironmentTag("production")).toBe("production");
    expect(parseEnvironmentTag("staging")).toBe("staging");
    expect(parseEnvironmentTag("prod-us-east")).toBe("prod-us-east");
    expect(parseEnvironmentTag("tenant_abc")).toBe("tenant_abc");
  });

  test("parseEnvironmentTag rejects empty, whitespace, and path-dangerous values", () => {
    expect(parseEnvironmentTag("")).toBeNull();
    expect(parseEnvironmentTag("  ")).toBeNull();
    expect(parseEnvironmentTag("prod/us")).toBeNull();
    expect(parseEnvironmentTag("..")).toBeNull();
  });

  test("defaultEnvironmentTag is 'production'", () => {
    expect(defaultEnvironmentTag()).toBe("production");
  });
});

describe("SuiteId", () => {
  test("parseSuiteId accepts typical ids", () => {
    expect(parseSuiteId("prod-eval-v3")).toBe("prod-eval-v3");
    expect(parseSuiteId("v1")).toBe("v1");
  });

  test("parseSuiteId rejects empty and path-dangerous values", () => {
    expect(parseSuiteId("")).toBeNull();
    expect(parseSuiteId("a/b")).toBeNull();
    expect(parseSuiteId("..")).toBeNull();
  });
});

describe("ContentHash", () => {
  test("parseContentHash accepts sha256:<64 lowercase hex>", () => {
    const h = "sha256:" + "0123456789abcdef".repeat(4);
    expect(parseContentHash(h)).toBe(h);
  });

  test("parseContentHash rejects wrong algorithm, wrong length, or non-hex", () => {
    expect(parseContentHash("")).toBeNull();
    expect(parseContentHash("sha256:short")).toBeNull();
    expect(parseContentHash("md5:" + "0".repeat(32))).toBeNull();
    expect(parseContentHash("sha256:" + "Z".repeat(64))).toBeNull(); // non-hex
    expect(parseContentHash("sha256:" + "0".repeat(63))).toBeNull(); // off-by-one
    // uppercase hex explicitly rejected — canonical form is lowercase
    expect(parseContentHash("sha256:" + "A".repeat(64))).toBeNull();
  });
});
