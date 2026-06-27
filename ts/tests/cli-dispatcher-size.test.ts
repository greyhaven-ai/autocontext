import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const repoRoot = join(import.meta.dirname, "..", "..");
const cliIndex = join(repoRoot, "ts", "src", "cli", "index.ts");

describe("CLI root dispatcher", () => {
  it("stays small and delegates command implementation", () => {
    const source = readFileSync(cliIndex, "utf8");

    expect(source.split("\n").length).toBeLessThan(250);
    expect(source).not.toContain("parseArgs");
  });
});
