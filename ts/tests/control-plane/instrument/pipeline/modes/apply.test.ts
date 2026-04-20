/**
 * A2-I Layer 6 - apply mode unit tests (spec §7.4).
 *
 * Tests in isolation — working-tree write, apply-log.json. Clean-tree preflight
 * is exercised via the orchestrator + preflight tests; apply mode itself
 * trusts the orchestrator to have performed the check.
 */
import { describe, test, expect, beforeEach } from "vitest";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
  rmSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runApplyMode } from "../../../../../src/control-plane/instrument/pipeline/modes/apply.js";

const scratches: string[] = [];
function scratch(): string {
  const d = mkdtempSync(join(tmpdir(), "a2i-apply-"));
  scratches.push(d);
  return d;
}

beforeEach(() => {
  while (scratches.length > 0) {
    const d = scratches.pop()!;
    try {
      rmSync(d, { recursive: true, force: true });
    } catch {
      // ignore
    }
  }
});

describe("runApplyMode - working-tree write", () => {
  test("writes afterContent to the correct working-tree path", () => {
    const cwd = scratch();
    mkdirSync(join(cwd, "src"), { recursive: true });
    writeFileSync(join(cwd, "src", "main.py"), "old content\n", "utf-8");
    const sessionDir = join(cwd, ".autocontext", "sess");
    mkdirSync(sessionDir, { recursive: true });

    const res = runApplyMode({
      cwd,
      sessionDir,
      patches: [
        { filePath: "src/main.py", afterContent: "new content\n" },
      ],
      sessionUlid: "01HN00",
      nowIso: "2026-04-17T12:00:00.000Z",
    });
    expect(res.filesWritten).toEqual(["src/main.py"]);
    expect(readFileSync(join(cwd, "src", "main.py"), "utf-8")).toBe("new content\n");
  });

  test("writes apply-log.json containing files + mode + timestamp", () => {
    const cwd = scratch();
    const sessionDir = join(cwd, "sess");
    mkdirSync(sessionDir, { recursive: true });
    runApplyMode({
      cwd,
      sessionDir,
      patches: [],
      sessionUlid: "01HN00",
      nowIso: "2026-04-17T12:00:00.000Z",
    });
    const logPath = join(sessionDir, "apply-log.json");
    expect(existsSync(logPath)).toBe(true);
    const parsed = JSON.parse(readFileSync(logPath, "utf-8"));
    expect(parsed).toMatchObject({
      sessionUlid: "01HN00",
      mode: "apply",
      completedAt: "2026-04-17T12:00:00.000Z",
      filesWritten: [],
    });
  });

  test("creates parent directories as needed for new files", () => {
    const cwd = scratch();
    const sessionDir = join(cwd, "sess");
    mkdirSync(sessionDir, { recursive: true });
    runApplyMode({
      cwd,
      sessionDir,
      patches: [
        { filePath: "new/sub/dir/x.ts", afterContent: "hello\n" },
      ],
      sessionUlid: "01HN00",
      nowIso: "2026-04-17T12:00:00.000Z",
    });
    expect(readFileSync(join(cwd, "new", "sub", "dir", "x.ts"), "utf-8")).toBe("hello\n");
  });
});
