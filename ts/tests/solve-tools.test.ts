import { describe, expect, it, vi } from "vitest";

import {
  buildSolveResultNotFoundPayload,
  registerSolveTools,
} from "../src/mcp/solve-tools.js";

function createFakeServer() {
  const registeredTools: Record<
    string,
    {
      description: string;
      schema: Record<string, unknown>;
      handler: (args: Record<string, unknown>) => Promise<{ content: Array<{ type: string; text: string }> }>;
    }
  > = {};

  return {
    registeredTools,
    tool(
      name: string,
      description: string,
      schema: Record<string, unknown>,
      handler: (args: Record<string, unknown>) => Promise<{ content: Array<{ type: string; text: string }> }>,
    ) {
      registeredTools[name] = { description, schema, handler };
    },
  };
}

describe("solve MCP tools", () => {
  it("submits solve jobs and returns pending payloads", async () => {
    const server = createFakeServer();
    const submit = vi.fn(() => "solve-123");

    registerSolveTools(server, {
      solveManager: {
        submit,
        getStatus: vi.fn(),
        getResult: vi.fn(),
      },
    });

    const result = await server.registeredTools.solve_scenario.handler({
      description: "grid ctf",
      generations: 2,
      family: "game",
      generation_time_budget: 10,
    });

    expect(submit).toHaveBeenCalledWith("grid ctf", 2, {
      familyOverride: "game",
      generationTimeBudgetSeconds: 10,
    });
    expect(JSON.parse(result.content[0].text)).toEqual({
      jobId: "solve-123",
      status: "pending",
    });
  });

  it("registers Python-compatible solve tool aliases", async () => {
    const server = createFakeServer();
    const submit = vi.fn(() => "solve-123");

    registerSolveTools(server, {
      solveManager: {
        submit,
        getStatus: vi.fn(() => ({ jobId: "solve-123", status: "completed" })),
        getResult: vi.fn(() => ({ scenario_name: "grid_ctf" })),
      },
    });

    expect(Object.keys(server.registeredTools).sort()).toEqual([
      "autocontext_solve_result",
      "autocontext_solve_scenario",
      "autocontext_solve_status",
      "solve_result",
      "solve_scenario",
      "solve_status",
    ]);

    const result = await server.registeredTools.autocontext_solve_scenario.handler({
      description: "grid ctf",
    });
    expect(submit).toHaveBeenCalledWith("grid ctf", 5);
    expect(JSON.parse(result.content[0].text)).toEqual({
      job_id: "solve-123",
      status: "pending",
    });

    const aliasStatus = await server.registeredTools.autocontext_solve_status.handler({
      job_id: "solve-123",
    });
    const canonicalStatus = await server.registeredTools.solve_status.handler({
      jobId: "solve-123",
    });
    expect(JSON.parse(aliasStatus.content[0].text)).toEqual({
      job_id: "solve-123",
      status: "completed",
    });
    expect(JSON.parse(canonicalStatus.content[0].text)).toEqual({
      jobId: "solve-123",
      status: "completed",
    });

    const aliasResult = await server.registeredTools.autocontext_solve_result.handler({
      job_id: "solve-123",
    });
    const canonicalResult = await server.registeredTools.solve_result.handler({
      jobId: "solve-123",
    });
    expect(aliasResult).toEqual(canonicalResult);
  });

  it("returns solve status payloads from the shared manager", async () => {
    const server = createFakeServer();

    registerSolveTools(server, {
      solveManager: {
        submit: vi.fn(),
        getStatus: vi.fn(() => ({
          jobId: "solve-123",
          status: "completed",
          scenarioName: "grid_ctf",
        })),
        getResult: vi.fn(),
      },
    });

    const result = await server.registeredTools.solve_status.handler({
      jobId: "solve-123",
    });

    expect(JSON.parse(result.content[0].text)).toEqual({
      jobId: "solve-123",
      status: "completed",
      scenarioName: "grid_ctf",
    });
  });

  it("returns completed solve results or stable not-found payloads", async () => {
    const server = createFakeServer();
    const getResult = vi
      .fn()
      .mockReturnValueOnce({ scenario_name: "grid_ctf", skill_markdown: "# Skill" })
      .mockReturnValueOnce(null);

    registerSolveTools(server, {
      solveManager: {
        submit: vi.fn(),
        getStatus: vi.fn(),
        getResult,
      },
    });

    const completed = await server.registeredTools.solve_result.handler({
      jobId: "solve-123",
    });
    expect(JSON.parse(completed.content[0].text)).toEqual({
      scenario_name: "grid_ctf",
      skill_markdown: "# Skill",
    });

    const missing = await server.registeredTools.solve_result.handler({
      jobId: "solve-missing",
    });
    expect(JSON.parse(missing.content[0].text)).toEqual(
      buildSolveResultNotFoundPayload("solve-missing"),
    );
  });
});
