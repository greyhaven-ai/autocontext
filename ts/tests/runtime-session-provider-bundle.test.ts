import { execFile, execFileSync, spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("node:child_process", () => ({
  execFile: vi.fn(),
  execFileSync: vi.fn(),
  spawn: vi.fn(),
}));

const execFileSyncMock = vi.mocked(execFileSync);
void execFile;
void spawn;

describe("runtime session provider bundle", () => {
  afterEach(() => {
    vi.resetAllMocks();
    vi.resetModules();
  });

  it("creates one persisted RuntimeSession for CLI-backed role providers", async () => {
    execFileSyncMock.mockImplementation(((_command: string, args?: readonly string[]) => {
      expect(args).toEqual([
        "exec",
        "--model",
        "o3",
        "--full-auto",
        "--quiet",
        "bundle task",
      ]);
      return "codex bundle output" as never;
    }) as unknown as typeof execFileSync);

    const dir = mkdtempSync(join(tmpdir(), "runtime-session-bundle-"));
    const dbPath = join(dir, "autocontext.sqlite3");
    const { buildRoleProviderBundle } = await import("../src/providers/role-provider-bundle.js");
    const { RuntimeSessionEventStore, RuntimeSessionEventType } =
      await import("../src/session/runtime-events.js");

    const bundle = buildRoleProviderBundle(
      {
        agentProvider: "codex",
        dbPath,
        codexModel: "o3",
        codexQuiet: true,
      },
      {},
      {
        runtimeSession: {
          sessionId: "run-1-runtime",
          goal: "autoctx run support_triage",
          workspaceRoot: dir,
          cwd: "workspace",
          metadata: {
            command: "run",
            runId: "run-1",
          },
        },
      },
    );

    const result = await bundle.defaultProvider.complete({
      systemPrompt: "",
      userPrompt: "bundle task",
    });
    bundle.close?.();

    expect(result.text).toBe("codex bundle output");
    expect(bundle.runtimeSession?.sessionId).toBe("run-1-runtime");

    const store = new RuntimeSessionEventStore(dbPath);
    const log = store.load("run-1-runtime");
    store.close();

    expect(log?.metadata).toMatchObject({
      goal: "autoctx run support_triage",
      command: "run",
      runId: "run-1",
    });
    expect(log?.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
      RuntimeSessionEventType.ASSISTANT_MESSAGE,
    ]);
    expect(log?.events[0].payload).toMatchObject({
      prompt: "bundle task",
      role: "default",
      cwd: "/workspace",
    });
    expect(log?.events[1].payload).toMatchObject({
      text: "codex bundle output",
      metadata: {
        runtime: "codex-cli",
        operation: "generate",
        runtimeSessionId: "run-1-runtime",
      },
    });
  });
});
