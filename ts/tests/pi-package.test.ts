import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";

import { buildPiPackage, writePiPackage } from "../src/knowledge/pi-package.js";

function strategyPackage(): Record<string, unknown> {
  return {
    format_version: 1,
    scenario_name: "grid_ctf",
    display_name: "Grid CTF",
    description: "Capture the flag on a grid.",
    playbook: "## Playbook\n\nScout, then strike.",
    lessons: ["Prefer short routes.", "Avoid stale scouts."],
    best_strategy: { aggression: 0.7 },
    best_score: 0.88,
    metadata: {},
    skill_markdown: "---\nname: grid-ctf\ndescription: Grid CTF knowledge\n---\n",
  };
}

describe("Pi-compatible package export", () => {
  let tempDir: string;

  beforeEach(() => {
    tempDir = mkdtempSync(join(tmpdir(), "autoctx-pi-package-"));
  });

  afterEach(() => {
    rmSync(tempDir, { recursive: true, force: true });
  });

  it("builds the same installable file layout as the Python runtime", () => {
    const pkg = buildPiPackage(strategyPackage(), "0.15.0");

    expect(pkg.packageDirName).toBe("grid-ctf-pi-package");
    expect(Object.keys(pkg.files)).toEqual([
      "README.md",
      "autocontext.package.json",
      "package.json",
      "prompts/grid-ctf.md",
      "skills/grid-ctf-knowledge/SKILL.md",
    ]);
    const manifest = JSON.parse(pkg.files["package.json"] ?? "{}") as {
      name: string;
      version: string;
      pi: { skills: string[]; prompts: string[] };
      autocontext: { scenario_name: string };
    };
    expect(manifest.name).toBe("autocontext-grid-ctf-pi-package");
    expect(manifest.version).toBe("0.15.0");
    expect(manifest.pi.skills).toEqual(["skills/grid-ctf-knowledge/SKILL.md"]);
    expect(manifest.pi.prompts).toEqual(["prompts/grid-ctf.md"]);
    expect(manifest.autocontext.scenario_name).toBe("grid_ctf");
    expect(pkg.files["prompts/grid-ctf.md"]).toContain("autocontext_export_package");
  });

  it("writes the package directory and reports every file", () => {
    const outputDir = join(tempDir, "pkg");
    const written = writePiPackage(buildPiPackage(strategyPackage(), "0.15.0"), outputDir);

    expect(written.outputDir).toBe(outputDir);
    expect(written.files.map((path) => relative(outputDir, path)).sort()).toEqual([
      "README.md",
      "autocontext.package.json",
      "package.json",
      "prompts/grid-ctf.md",
      "skills/grid-ctf-knowledge/SKILL.md",
    ]);
    expect(readFileSync(join(outputDir, "skills/grid-ctf-knowledge/SKILL.md"), "utf-8"))
      .toContain("name: grid-ctf");
  });
});
