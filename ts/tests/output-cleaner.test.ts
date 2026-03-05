import { describe, it, expect } from "vitest";
import { cleanRevisionOutput } from "../src/execution/output-cleaner.js";

describe("cleanRevisionOutput", () => {
  it("strips ## Revised Output header and **Analysis:** block", () => {
    const input =
      "## Revised Output\n\nHello world\n\n**Analysis:**\n- Good stuff";
    expect(cleanRevisionOutput(input)).toBe("Hello world");
  });

  it("strips ## Key Changes Made section", () => {
    const input =
      "The actual content here.\n\n## Key Changes Made\n- Changed X";
    expect(cleanRevisionOutput(input)).toBe("The actual content here.");
  });

  it("strips **Analysis:** block", () => {
    const input =
      "My haiku here\n\n**Analysis:**\n- Syllable count: 5-7-5";
    expect(cleanRevisionOutput(input)).toBe("My haiku here");
  });

  it("passes through clean content unchanged", () => {
    const input = "Just clean content\nNo metadata";
    expect(cleanRevisionOutput(input)).toBe("Just clean content\nNo metadata");
  });

  it("handles combined header + Analysis + Key Changes", () => {
    const input =
      "## Revised Output\n\nGood content\n\n**Analysis:**\n- Note\n\n## Key Changes Made\n- Change";
    expect(cleanRevisionOutput(input)).toBe("Good content");
  });

  it("strips ## Analysis section", () => {
    const input = "Content here\n\n## Analysis\nSome analysis text";
    expect(cleanRevisionOutput(input)).toBe("Content here");
  });

  it("strips ## Changes section", () => {
    const input = "Content here\n\n## Changes\n- Item 1\n- Item 2";
    expect(cleanRevisionOutput(input)).toBe("Content here");
  });

  it("strips ## Improvements section", () => {
    const input = "Content here\n\n## Improvements\n1. Better flow";
    expect(cleanRevisionOutput(input)).toBe("Content here");
  });

  it("strips ## Self-Assessment section", () => {
    const input = "Content here\n\n## Self-Assessment\nI improved X";
    expect(cleanRevisionOutput(input)).toBe("Content here");
  });

  it("strips trailing 'This revision transforms...' paragraph", () => {
    const input =
      "The revised content\n\nThis revision transforms the original by adding detail.";
    expect(cleanRevisionOutput(input)).toBe("The revised content");
  });

  it("strips trailing 'This revision improves...' paragraph", () => {
    const input =
      "The revised content\n\nThis revision improves clarity and flow.";
    expect(cleanRevisionOutput(input)).toBe("The revised content");
  });

  it("strips trailing 'This revision addresses...' paragraph", () => {
    const input =
      "The revised content\n\nThis revision addresses all feedback points.";
    expect(cleanRevisionOutput(input)).toBe("The revised content");
  });

  it("strips trailing 'This revision fixes...' paragraph", () => {
    const input =
      "The revised content\n\nThis revision fixes the structural issues noted.";
    expect(cleanRevisionOutput(input)).toBe("The revised content");
  });

  it("returns empty string for metadata-only output", () => {
    const input = "## Revised Output\n\n## Key Changes Made\n- Change 1";
    expect(cleanRevisionOutput(input)).toBe("");
  });

  it("handles output with no trailing newline", () => {
    const input = "Clean content";
    expect(cleanRevisionOutput(input)).toBe("Clean content");
  });
});
