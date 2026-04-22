/**
 * content.test.ts — Tests for content-block flattening and tool-use extraction.
 * Mirrors Python test_content.py (10 tests).
 */
import { describe, test, expect } from "vitest";
import { flattenContent, extractToolUses } from "../../../src/integrations/anthropic/content.js";

describe("flattenContent", () => {
  test("string passthrough", () => {
    expect(flattenContent("hi")).toBe("hi");
  });

  test("empty array returns empty string", () => {
    expect(flattenContent([])).toBe("");
  });

  test("single text block returns text", () => {
    expect(flattenContent([{ type: "text", text: "a" }])).toBe("a");
  });

  test("multiple text blocks concatenated", () => {
    expect(flattenContent([
      { type: "text", text: "hello" },
      { type: "text", text: " world" },
    ])).toBe("hello world");
  });

  test("image blocks dropped", () => {
    expect(flattenContent([
      { type: "image" },
      { type: "text", text: "only text" },
    ])).toBe("only text");
  });

  test("tool_use blocks dropped", () => {
    expect(flattenContent([
      { type: "tool_use", name: "fn", input: {} },
      { type: "text", text: "text only" },
    ])).toBe("text only");
  });
});

describe("extractToolUses", () => {
  test("string input returns null", () => {
    expect(extractToolUses("hi")).toBeNull();
  });

  test("array with only text returns null", () => {
    expect(extractToolUses([{ type: "text", text: "x" }])).toBeNull();
  });

  test("array with tool_use returns extracted calls", () => {
    const result = extractToolUses([{ type: "tool_use", name: "f", input: { x: 1 } }]);
    expect(result).toEqual([{ toolName: "f", args: { x: 1 } }]);
  });

  test("mix of text and tool_use: extract only tool_use", () => {
    const result = extractToolUses([
      { type: "text", text: "some text" },
      { type: "tool_use", name: "search", input: { query: "foo" } },
    ]);
    expect(result).toEqual([{ toolName: "search", args: { query: "foo" } }]);
  });
});
