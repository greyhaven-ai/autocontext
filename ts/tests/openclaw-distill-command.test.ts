import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { AppSettingsSchema } from "../src/config/index.js";
import {
  expandDistillSidecarCommand,
  OpenClawService,
  parseDistillSidecarCommand,
} from "../src/openclaw/service.js";

vi.mock("node:child_process", () => ({
  spawn: vi.fn(),
}));

const spawnMock = vi.mocked(spawn);
const temporaryRoots: string[] = [];

function fakeChildProcess(): ReturnType<typeof spawn> & { unref: ReturnType<typeof vi.fn> } {
  const child = new EventEmitter() as ReturnType<typeof spawn> & {
    unref: ReturnType<typeof vi.fn>;
  };
  child.unref = vi.fn(() => child);
  return child;
}

afterEach(() => {
  vi.clearAllMocks();
  for (const root of temporaryRoots.splice(0)) {
    rmSync(root, { recursive: true, force: true });
  }
});

describe("OpenClaw distill sidecar argv templates", () => {
  it("parses a JSON argv array without shell tokenization", () => {
    expect(parseDistillSidecarCommand(JSON.stringify([
      "distill worker",
      "--scenario",
      "{scenario}",
    ]))).toEqual([
      "distill worker",
      "--scenario",
      "{scenario}",
    ]);
  });

  it.each([
    "distill-worker --scenario {scenario}",
    JSON.stringify("distill-worker"),
    JSON.stringify([]),
    JSON.stringify(["distill-worker", 42]),
    JSON.stringify(["distill-worker", ""]),
    JSON.stringify(["distill-worker", "bad\0argument"]),
    JSON.stringify(["distill-worker", "prefix-{scenario}"]),
    JSON.stringify(["distill-worker", "{unknown}"]),
    JSON.stringify(["{scenario}", "--run"]),
    JSON.stringify(["{job_id}", "--run"]),
  ])("rejects unsafe command specification %s", (rawCommand) => {
    expect(() => parseDistillSidecarCommand(rawCommand)).toThrow();
  });

  it("preserves hostile scenario text as exactly one argv entry", () => {
    const template = parseDistillSidecarCommand(JSON.stringify([
      "distill-worker",
      "--job",
      "{job_id}",
      "--scenario",
      "{scenario}",
    ]));
    const hostileScenario = "grid; touch /tmp/not-created $(id) --extra-flag";

    const command = expandDistillSidecarCommand(template, {
      job_id: "job-123",
      scenario: hostileScenario,
    });

    expect(command).toEqual([
      "distill-worker",
      "--job",
      "job-123",
      "--scenario",
      hostileScenario,
    ]);
  });

  it.each(["--inspect", "  --inspect"])(
    "rejects option-shaped placeholder value %j",
    (scenario) => {
      expect(() => expandDistillSidecarCommand(
        ["distill-worker", "{scenario}"],
        { job_id: "job-123", scenario },
      )).toThrow("option-shaped");
    },
  );

  it("does not treat an unused value as an argv option", () => {
    expect(expandDistillSidecarCommand(
      ["distill-worker", "--fixed-mode"],
      { job_id: "job-123", scenario: "--not-expanded" },
    )).toEqual(["distill-worker", "--fixed-mode"]);
  });

  it("rejects NUL placeholder values before process creation", () => {
    expect(() => expandDistillSidecarCommand(
      ["distill-worker", "{scenario}"],
      { job_id: "job-123", scenario: "bad\0scenario" },
    )).toThrow("NUL");
  });

  it("passes expanded values to spawn as fixed argv with shell disabled", () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-openclaw-distill-"));
    temporaryRoots.push(root);
    const child = fakeChildProcess();
    spawnMock.mockReturnValue(child);
    const settings = AppSettingsSchema.parse({
      openclawDistillSidecarCommand: JSON.stringify([
        "distill-worker",
        "--job",
        "{job_id}",
        "--scenario",
        "{scenario}",
      ]),
    });
    const service = new OpenClawService({
      knowledgeRoot: join(root, "knowledge"),
      settings,
      openStore: () => {
        throw new Error("not used");
      },
    });
    const hostileScenario = "grid; touch /tmp/not-created $(id) --extra-flag";

    const result = service.triggerDistillation({ scenario: hostileScenario });

    expect(result.status).toBe("pending");
    expect(spawnMock).toHaveBeenCalledTimes(1);
    const [file, args, options] = spawnMock.mock.calls[0]!;
    expect(file).toBe("distill-worker");
    expect(args).toEqual([
      "--job",
      result.job_id,
      "--scenario",
      hostileScenario,
    ]);
    expect(options).toMatchObject({ shell: false, detached: true, stdio: "ignore" });
    expect(child.unref).not.toHaveBeenCalled();
    expect(service.getDistillJob(result.job_id as string)?.status).toBe("pending");

    child.emit("spawn");

    expect(service.getDistillJob(result.job_id as string)?.status).toBe("running");
    expect(child.unref).toHaveBeenCalledTimes(1);
  });

  it.each(["ENOENT", "EACCES"])(
    "handles asynchronous spawn %s errors without an unhandled EventEmitter error",
    (code) => {
      const root = mkdtempSync(join(tmpdir(), "autoctx-openclaw-distill-error-"));
      temporaryRoots.push(root);
      const child = fakeChildProcess();
      spawnMock.mockReturnValue(child);
      const service = new OpenClawService({
        knowledgeRoot: join(root, "knowledge"),
        settings: AppSettingsSchema.parse({
          openclawDistillSidecarCommand: JSON.stringify(["missing-distill-worker"]),
        }),
        openStore: () => {
          throw new Error("not used");
        },
      });
      const result = service.triggerDistillation({ scenario: "grid_ctf" });
      const error = Object.assign(new Error(`${code}: sidecar launch failed`), { code });

      expect(result.status).toBe("pending");
      expect(() => child.emit("error", error)).not.toThrow();
      expect(service.getDistillJob(result.job_id as string)).toMatchObject({
        status: "failed",
        error_message: expect.stringContaining(code),
      });
      expect(child.unref).not.toHaveBeenCalled();
    },
  );

  it("fails the job without spawning when a scenario is option-shaped", () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-openclaw-distill-"));
    temporaryRoots.push(root);
    const settings = AppSettingsSchema.parse({
      openclawDistillSidecarCommand: JSON.stringify([
        "distill-worker",
        "--scenario",
        "{scenario}",
      ]),
    });
    const service = new OpenClawService({
      knowledgeRoot: join(root, "knowledge"),
      settings,
      openStore: () => {
        throw new Error("not used");
      },
    });

    const result = service.triggerDistillation({ scenario: " --inspect" });

    expect(result).toMatchObject({
      status: "failed",
      scenario: "--inspect",
    });
    expect(result.error).toContain("option-shaped");
    expect(spawnMock).not.toHaveBeenCalled();
  });
});
