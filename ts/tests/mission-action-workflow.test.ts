import { describe, expect, it, vi } from "vitest";

import {
  buildMissionRunRequest,
  executeMissionActionRequest,
} from "../src/server/mission-action-workflow.js";

describe("mission action workflow", () => {
  it("builds mission run requests with normalized iterations and provider rules", () => {
    const provider = { complete: vi.fn() };

    expect(buildMissionRunRequest({
      body: { maxIterations: "3", stepDescription: "Advance the plan" },
      mission: { metadata: { missionType: "research" } },
      buildMissionProvider: () => provider,
    })).toEqual({
      maxIterations: 3,
      stepDescription: "Advance the plan",
      provider,
    });

    expect(buildMissionRunRequest({
      body: { maxIterations: 0 },
      mission: { metadata: { missionType: "code" } },
      buildMissionProvider: () => provider,
    })).toEqual({
      maxIterations: 1,
      stepDescription: undefined,
      provider: undefined,
    });
  });

  it("returns 404 when the mission does not exist", async () => {
    await expect(executeMissionActionRequest({
      action: "pause",
      missionId: "missing",
      body: {},
      missionManager: {
        get: () => null,
        pause: vi.fn(),
        resume: vi.fn(),
        cancel: vi.fn(),
      },
      runManager: {
        getRunsRoot: () => "/tmp/runs",
        getKnowledgeRoot: () => "/tmp/knowledge",
        buildMissionProvider: vi.fn(),
      },
    })).resolves.toEqual({
      status: 404,
      body: { error: "Mission 'missing' not found" },
    });
  });

  it("runs mission loops with normalized options", async () => {
    const runMissionLoop = vi.fn(async () => ({ finalStatus: "completed", checkpointPath: "/tmp/checkpoint.json" }));
    const closeProvider = vi.fn();

    await expect(executeMissionActionRequest({
      action: "run",
      missionId: "mission_1",
      body: { maxIterations: "2", stepDescription: "Advance once" },
      missionManager: {
        get: () => ({ id: "mission_1", metadata: { missionType: "research" } }),
        pause: vi.fn(),
        resume: vi.fn(),
        cancel: vi.fn(),
      },
      runManager: {
        getRunsRoot: () => "/tmp/runs",
        getKnowledgeRoot: () => "/tmp/knowledge",
        buildMissionProvider: () => ({ complete: vi.fn(), close: closeProvider }),
      },
      deps: {
        runMissionLoop,
        buildMissionStatusPayload: vi.fn(),
        writeMissionCheckpoint: vi.fn(),
      },
    })).resolves.toEqual({
      status: 200,
      body: { finalStatus: "completed", checkpointPath: "/tmp/checkpoint.json" },
    });

    expect(runMissionLoop).toHaveBeenCalledOnce();
    const runMissionLoopMock = runMissionLoop as unknown as { mock: { calls: unknown[][] } };
    const runMissionLoopCall = runMissionLoopMock.mock.calls[0];
    expect(runMissionLoopCall?.[4]).toMatchObject({
      maxIterations: 2,
      stepDescription: "Advance once",
    });
    expect(closeProvider).toHaveBeenCalledOnce();
  });

  it("applies pause/resume/cancel mission controls and returns checkpointed status", async () => {
    const pause = vi.fn();
    const resume = vi.fn();
    const cancel = vi.fn();
    const buildMissionStatusPayload = vi.fn(() => ({ id: "mission_1", status: "paused" }));
    const writeMissionCheckpoint = vi.fn(() => "/tmp/checkpoint.json");

    await expect(executeMissionActionRequest({
      action: "pause",
      missionId: "mission_1",
      body: {},
      missionManager: {
        get: () => ({ id: "mission_1", metadata: {} }),
        pause,
        resume,
        cancel,
      },
      runManager: {
        getRunsRoot: () => "/tmp/runs",
        getKnowledgeRoot: () => "/tmp/knowledge",
        buildMissionProvider: vi.fn(),
      },
      deps: {
        runMissionLoop: vi.fn(),
        buildMissionStatusPayload,
        writeMissionCheckpoint,
      },
    })).resolves.toEqual({
      status: 200,
      body: {
        id: "mission_1",
        status: "paused",
        checkpointPath: "/tmp/checkpoint.json",
      },
    });

    expect(pause).toHaveBeenCalledWith("mission_1");
    expect(writeMissionCheckpoint).toHaveBeenCalledWith(expect.anything(), "mission_1", "/tmp/runs");
  });
});
