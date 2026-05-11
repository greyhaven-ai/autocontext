import { mkdirSync, mkdtempSync, realpathSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  RUNTIME_CONTEXT_LAYER_KEYS,
  RUNTIME_CONTEXT_LAYERS,
  RuntimeContextLayerKey,
  RuntimeContextDiscoveryRequest,
  discoverRepoInstructions,
  discoverRuntimeSkills,
  selectRuntimeKnowledgeComponents,
} from "../src/session/runtime-context.js";

function writeSkill(root: string, name: string, description: string): string {
  const dir = join(root, name);
  mkdirSync(dir, { recursive: true });
  writeFileSync(
    join(dir, "SKILL.md"),
    `---\nname: ${name}\ndescription: ${description}\n---\n\n# ${name}\n\nInstructions for ${name}.\n`,
  );
  return dir;
}

describe("runtime context layers", () => {
  it("exposes canonical assembly order", () => {
    expect(RUNTIME_CONTEXT_LAYER_KEYS).toEqual([
      RuntimeContextLayerKey.SYSTEM_POLICY,
      RuntimeContextLayerKey.REPO_INSTRUCTIONS,
      RuntimeContextLayerKey.ROLE_INSTRUCTIONS,
      RuntimeContextLayerKey.SCENARIO_CONTEXT,
      RuntimeContextLayerKey.KNOWLEDGE,
      RuntimeContextLayerKey.RUNTIME_SKILLS,
      RuntimeContextLayerKey.TOOL_AFFORDANCES,
      RuntimeContextLayerKey.SESSION_HISTORY,
    ]);
    expect(RUNTIME_CONTEXT_LAYERS.map((layer) => layer.order)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(RUNTIME_CONTEXT_LAYERS.find((layer) => layer.key === RuntimeContextLayerKey.KNOWLEDGE)?.budget).toBe("compress");
    expect(
      RUNTIME_CONTEXT_LAYERS.find((layer) => layer.key === RuntimeContextLayerKey.SESSION_HISTORY)?.childTaskBehavior,
    ).toBe("recompute_from_child_session");
  });

  it("discovers repo instructions safely and recomputes for child cwd", () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-context-"));
    const request = new RuntimeContextDiscoveryRequest({ workspaceRoot: root, cwd: "/pkg" });

    expect(discoverRepoInstructions(request)).toEqual([]);

    writeFileSync(join(root, "AGENTS.md"), "root agents\n");
    mkdirSync(join(root, "pkg"), { recursive: true });
    writeFileSync(join(root, "pkg", "CLAUDE.md"), "pkg claude\n");
    mkdirSync(join(root, "other"), { recursive: true });
    writeFileSync(join(root, "other", "AGENTS.md"), "other agents\n");

    const parentInstructions = discoverRepoInstructions(request);
    const childInstructions = discoverRepoInstructions(request.forChildTask("/other"));

    expect(parentInstructions.map((instruction) => instruction.relativePath)).toEqual(["AGENTS.md", "pkg/CLAUDE.md"]);
    expect(parentInstructions.map((instruction) => instruction.content)).toEqual(["root agents\n", "pkg claude\n"]);
    expect(childInstructions.map((instruction) => instruction.relativePath)).toEqual(["AGENTS.md", "other/AGENTS.md"]);
  });

  it("rejects cwd symlinks that resolve outside the workspace", () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-context-"));
    const outside = mkdtempSync(join(tmpdir(), "autoctx-outside-"));
    writeFileSync(join(outside, "AGENTS.md"), "outside agents\n");
    writeSkill(join(outside, ".claude", "skills"), "outside-only", "outside skill");
    symlinkSync(outside, join(root, "link"), "dir");

    const request = new RuntimeContextDiscoveryRequest({ workspaceRoot: root, cwd: "/link" });

    expect(() => discoverRepoInstructions(request)).toThrow(/escapes workspace root/);
    expect(() => discoverRuntimeSkills(request)).toThrow(/escapes workspace root/);
  });

  it("discovers cwd-specific skills and deduplicates by nearest cwd", () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-context-"));
    const rootSkills = join(root, ".claude", "skills");
    const pkgSkills = join(root, "pkg", ".claude", "skills");
    writeSkill(rootSkills, "shared", "root shared");
    writeSkill(rootSkills, "root-only", "root only");
    const pkgShared = writeSkill(pkgSkills, "shared", "package shared");
    writeSkill(pkgSkills, "pkg-only", "package only");

    const registry = discoverRuntimeSkills(new RuntimeContextDiscoveryRequest({ workspaceRoot: root, cwd: "/pkg" }));

    expect(registry.allManifests().map((manifest) => manifest.name)).toEqual(["pkg-only", "shared", "root-only"]);
    expect(registry.get("shared")?.manifest.skillPath).toBe(realpathSync(pkgShared));
    expect(
      discoverRuntimeSkills(new RuntimeContextDiscoveryRequest({ workspaceRoot: root, cwd: "/" }))
        .allManifests()
        .map((manifest) => manifest.name),
    ).toEqual(["root-only", "shared"]);
  });

  it("selects knowledge by include/exclude policy and skips empty values", () => {
    expect(
      selectRuntimeKnowledgeComponents(
        {
          playbook: "Use validated strategy.",
          hints: "",
          lessons: "Lesson one.",
          dead_ends: "Avoid stale path.",
          private_notes: "do not include",
        },
        { include: ["playbook", "hints", "lessons", "dead_ends"], exclude: ["lessons"] },
      ),
    ).toEqual({
      playbook: "Use validated strategy.",
      dead_ends: "Avoid stale path.",
    });
  });
});
