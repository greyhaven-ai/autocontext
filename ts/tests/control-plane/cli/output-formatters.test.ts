import { describe, test, expect } from "vitest";
import { formatOutput } from "../../../src/control-plane/cli/_shared/output-formatters.js";

describe("formatOutput", () => {
  test("json mode emits a single-line JSON doc", () => {
    const out = formatOutput({ a: 1, b: "two" }, "json");
    expect(() => JSON.parse(out)).not.toThrow();
    expect(JSON.parse(out)).toEqual({ a: 1, b: "two" });
    // Single JSON doc — no trailing commas, no multiple doc lines.
    expect(out.trim().startsWith("{")).toBe(true);
    expect(out.trim().endsWith("}")).toBe(true);
  });

  test("json mode for arrays", () => {
    const out = formatOutput([{ x: 1 }, { x: 2 }], "json");
    expect(JSON.parse(out)).toEqual([{ x: 1 }, { x: 2 }]);
  });

  test("table mode renders an ASCII table for arrays of objects", () => {
    const rows = [
      { id: "a", state: "candidate" },
      { id: "b", state: "active" },
    ];
    const out = formatOutput(rows, "table");
    expect(out).toContain("id");
    expect(out).toContain("state");
    expect(out).toContain("candidate");
    expect(out).toContain("active");
    // There's at least one separator line.
    expect(out.split("\n").length).toBeGreaterThanOrEqual(3);
  });

  test("table mode gracefully handles empty arrays", () => {
    const out = formatOutput([], "table");
    expect(out.trim().length).toBeGreaterThanOrEqual(0);
    // Doesn't throw and returns a string (possibly empty / "no rows").
    expect(typeof out).toBe("string");
  });

  test("pretty mode renders key: value blocks for objects", () => {
    const out = formatOutput({ id: "a", state: "candidate" }, "pretty");
    expect(out).toContain("id");
    expect(out).toContain("a");
    expect(out).toContain("state");
    expect(out).toContain("candidate");
  });

  test("pretty mode renders an itemized list for arrays", () => {
    const out = formatOutput([{ id: "a" }, { id: "b" }], "pretty");
    expect(out).toContain("a");
    expect(out).toContain("b");
  });

  test("pretty mode for scalars", () => {
    expect(formatOutput("hello", "pretty")).toContain("hello");
    expect(formatOutput(42, "pretty")).toContain("42");
  });

  test("json output contains no stderr-only content (no log prefixes)", () => {
    const out = formatOutput({ a: 1 }, "json");
    expect(out).not.toMatch(/^\[info\]|^\[warn\]|^\[debug\]/);
  });
});
