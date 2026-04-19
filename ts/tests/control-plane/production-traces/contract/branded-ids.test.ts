import { describe, test, expect } from "vitest";
import {
  newProductionTraceId,
  parseProductionTraceId,
  parseAppId,
  parseUserIdHash,
  parseSessionIdHash,
  parseFeedbackRefId,
} from "../../../../src/production-traces/contract/branded-ids.js";

describe("ProductionTraceId", () => {
  test("newProductionTraceId produces a valid ULID that round-trips through parseProductionTraceId", () => {
    const id = newProductionTraceId();
    expect(id).toHaveLength(26);
    const parsed = parseProductionTraceId(id as unknown as string);
    expect(parsed).toBe(id);
  });

  test("newProductionTraceId produces unique values across calls", () => {
    const a = newProductionTraceId();
    const b = newProductionTraceId();
    expect(a).not.toBe(b);
  });

  test("parseProductionTraceId rejects invalid input", () => {
    expect(parseProductionTraceId("")).toBeNull();
    expect(parseProductionTraceId("not-a-ulid")).toBeNull();
    expect(parseProductionTraceId("01J2XYZ7K8L9M0N1P2Q3R4")).toBeNull(); // 22 chars, too short
    expect(parseProductionTraceId("01J2XYZ7K8L9M0N1P2Q3R4S5T6X")).toBeNull(); // 27 chars, too long
    expect(parseProductionTraceId("01J2XYZ7K8L9M0N1P2Q3R4S5TI")).toBeNull(); // contains 'I' — Crockford excludes I/L/O/U
    expect(parseProductionTraceId("01j2xyz7k8l9m0n1p2q3r4s5t6")).toBeNull(); // lowercase not allowed
  });
});

describe("AppId", () => {
  test("parseAppId accepts slug-like non-empty strings", () => {
    expect(parseAppId("my-app")).toBe("my-app");
    expect(parseAppId("checkout_service")).toBe("checkout_service");
    expect(parseAppId("a")).toBe("a");
    expect(parseAppId("app-v2")).toBe("app-v2");
  });

  test("parseAppId rejects empty, whitespace, uppercase, and path-dangerous values", () => {
    expect(parseAppId("")).toBeNull();
    expect(parseAppId("  ")).toBeNull();
    expect(parseAppId("My-App")).toBeNull();
    expect(parseAppId("my/app")).toBeNull();
    expect(parseAppId("..")).toBeNull();
    expect(parseAppId("-leading-dash")).toBeNull();
  });
});

describe("UserIdHash", () => {
  test("parseUserIdHash accepts 64-char lowercase hex", () => {
    const h = "0123456789abcdef".repeat(4);
    expect(parseUserIdHash(h)).toBe(h);
  });

  test("parseUserIdHash rejects wrong length, wrong case, or non-hex", () => {
    expect(parseUserIdHash("")).toBeNull();
    expect(parseUserIdHash("0".repeat(63))).toBeNull(); // off-by-one
    expect(parseUserIdHash("0".repeat(65))).toBeNull(); // too long
    expect(parseUserIdHash("A".repeat(64))).toBeNull(); // uppercase
    expect(parseUserIdHash("Z".repeat(64))).toBeNull(); // non-hex
    expect(parseUserIdHash("sha256:" + "a".repeat(64))).toBeNull(); // carries prefix
  });
});

describe("SessionIdHash", () => {
  test("parseSessionIdHash accepts 64-char lowercase hex", () => {
    const h = "abcdef1234567890".repeat(4);
    expect(parseSessionIdHash(h)).toBe(h);
  });

  test("parseSessionIdHash rejects invalid", () => {
    expect(parseSessionIdHash("")).toBeNull();
    expect(parseSessionIdHash("abc")).toBeNull();
    expect(parseSessionIdHash("A".repeat(64))).toBeNull();
  });
});

describe("FeedbackRefId", () => {
  test("parseFeedbackRefId accepts any non-empty string (opaque)", () => {
    expect(parseFeedbackRefId("feedback-123")).toBe("feedback-123");
    expect(parseFeedbackRefId("https://example.com/fb/42")).toBe("https://example.com/fb/42");
    expect(parseFeedbackRefId("arbitrary opaque value")).toBe("arbitrary opaque value");
  });

  test("parseFeedbackRefId rejects empty-only-whitespace", () => {
    expect(parseFeedbackRefId("")).toBeNull();
    expect(parseFeedbackRefId("   ")).toBeNull();
  });
});

describe("re-exports from control-plane/contract/", () => {
  test("EnvironmentTag, ContentHash, Scenario parsers are re-exported for ergonomic downstream use", async () => {
    const m = await import("../../../../src/production-traces/contract/branded-ids.js");
    expect(typeof m.parseEnvironmentTag).toBe("function");
    expect(typeof m.parseContentHash).toBe("function");
    expect(typeof m.parseScenario).toBe("function");
    // Sanity: delegate to underlying parsers (same behavior).
    expect(m.parseEnvironmentTag("production")).toBe("production");
    expect(m.parseScenario("grid_ctf")).toBe("grid_ctf");
  });
});
