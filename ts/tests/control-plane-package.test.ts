import { describe, expect, it } from "vitest";
import type {
  AppId,
  EnvironmentTag,
  FeedbackRef,
  FeedbackRefId,
  ProductionTrace,
  ProductionTraceId,
  ProviderInfo,
  ResearchAdapter,
  Scenario,
  SessionIdHash,
  TraceSource,
  UserIdHash,
} from "../../packages/ts/control-plane/src/index.ts";
import {
  AckMsgSchema,
  Citation,
  ErrorMsgSchema,
  ExecutorInfoSchema,
  ExecutorResourcesSchema,
  HelloMsgSchema,
  PRODUCTION_TRACE_SCHEMA_VERSION,
  PROTOCOL_VERSION,
  StateMsgSchema,
  packageRole,
  packageTopologyVersion,
  ResearchConfig,
  ResearchQuery,
  ResearchResult,
  ScenarioInfoSchema,
  ScoringComponentSchema,
  StrategyParamSchema,
  Urgency,
} from "../../packages/ts/control-plane/src/index.ts";

describe("@autocontext/control-plane facade", () => {
  it("preserves the control package identity", () => {
    expect(packageRole).toBe("control");
    expect(packageTopologyVersion).toBe(1);
  });

  it("re-exports research domain contracts", () => {
    const urgency = Urgency.HIGH;
    const query = new ResearchQuery({
      topic: "refund policy changes",
      context: "customer support escalation",
      urgency,
      maxResults: 3,
      constraints: ["cite primary sources"],
      scenarioFamily: "agent_task",
      metadata: { ticket: "t-1" },
    });
    const citation = new Citation({
      source: "policy handbook",
      url: "https://example.com/policy",
      relevance: 0.95,
      snippet: "Refunds require manager sign-off after 30 days.",
      retrievedAt: "2026-04-25T00:00:00Z",
    });
    const adapter: ResearchAdapter = {
      search(input: ResearchQuery): ResearchResult {
        return new ResearchResult({
          queryTopic: input.topic,
          summary: "Manager sign-off required after 30 days.",
          citations: [citation],
          confidence: 0.91,
          metadata: { adapter: "demo" },
        });
      },
    };
    const result = adapter.search(query);
    const config = new ResearchConfig({
      enabled: true,
      adapterName: "demo",
      maxQueriesPerTurn: 1,
    });

    expect(query.urgency).toBe(Urgency.HIGH);
    expect(query.constraints).toEqual(["cite primary sources"]);
    expect(result.hasCitations).toBe(true);
    expect(result.citations[0]?.source).toBe("policy handbook");
    expect(result.metadata).toMatchObject({ adapter: "demo" });
    expect(config.enabled).toBe(true);
    expect(config.adapterName).toBe("demo");
    expect(config.maxQueriesPerTurn).toBe(1);
  });

  it("re-exports shared server protocol models", () => {
    const scenario = ScenarioInfoSchema.parse({
      name: "grid_ctf",
      description: "Capture the flag",
    });
    const resources = ExecutorResourcesSchema.parse({
      docker_image: "ghcr.io/greyhaven/executor:latest",
      cpu_cores: 4,
      memory_gb: 8,
      disk_gb: 20,
      timeout_minutes: 15,
    });
    const executor = ExecutorInfoSchema.parse({
      mode: "docker",
      available: true,
      description: "Local Docker executor",
      resources,
    });
    const param = StrategyParamSchema.parse({
      name: "aggression",
      description: "How aggressively to pursue flags",
    });
    const scoring = ScoringComponentSchema.parse({
      name: "win_rate",
      description: "Percent of matches won",
      weight: 0.7,
    });

    expect(PROTOCOL_VERSION).toBe(1);
    expect(scenario.name).toBe("grid_ctf");
    expect(executor.resources?.cpu_cores).toBe(4);
    expect(param.name).toBe("aggression");
    expect(scoring.weight).toBe(0.7);
  });

  it("re-exports basic server protocol message models", () => {
    const hello = HelloMsgSchema.parse({
      type: "hello",
      protocol_version: PROTOCOL_VERSION,
    });
    const state = StateMsgSchema.parse({
      type: "state",
      paused: true,
      generation: 3,
      phase: "evaluation",
    });
    const ack = AckMsgSchema.parse({
      type: "ack",
      action: "pause",
      decision: "accepted",
    });
    const error = ErrorMsgSchema.parse({
      type: "error",
      message: "run failed",
    });

    expect(hello.protocol_version).toBe(1);
    expect(state.paused).toBe(true);
    expect(state.generation).toBe(3);
    expect(state.phase).toBe("evaluation");
    expect(ack.action).toBe("pause");
    expect(ack.decision).toBe("accepted");
    expect(error.message).toBe("run failed");
  });

  it("re-exports production trace contract types", () => {
    const source: TraceSource = {
      emitter: "gateway",
      sdk: { name: "autoctx", version: "0.1.0" },
      hostname: "box-1",
    };
    const provider: ProviderInfo = {
      name: "anthropic",
      endpoint: "https://api.anthropic.com",
      providerVersion: "2026-04",
    };
    const feedback: FeedbackRef = {
      kind: "rating",
      submittedAt: "2026-04-25T00:00:02Z",
      ref: "feedback-1" as FeedbackRefId,
      score: 0.9,
      comment: "great help",
    };
    const trace: ProductionTrace = {
      schemaVersion: PRODUCTION_TRACE_SCHEMA_VERSION,
      traceId: "01ARZ3NDEKTSV4RRFFQ69G5FAV" as ProductionTraceId,
      source,
      provider,
      model: "claude-sonnet",
      session: {
        userIdHash: "a".repeat(64) as UserIdHash,
        sessionIdHash: "b".repeat(64) as SessionIdHash,
        requestId: "req-1",
      },
      env: {
        environmentTag: "prod" as EnvironmentTag,
        appId: "support-bot" as AppId,
        taskType: "triage",
        deploymentMeta: { region: "us-east-1" },
      },
      messages: [
        {
          role: "user",
          content: "help me with a refund",
          timestamp: "2026-04-25T00:00:00Z",
          toolCalls: [
            {
              toolName: "kb.search",
              args: { query: "refund" },
              durationMs: 12,
            },
          ],
          metadata: { lang: "en" },
        },
      ],
      toolCalls: [
        {
          toolName: "kb.search",
          args: { query: "refund" },
          result: { hits: 1 },
        },
      ],
      outcome: {
        label: "success",
        score: 0.9,
        reasoning: "resolved",
        signals: { accuracy: 0.9 },
        error: { type: "none", message: "no error" },
      },
      timing: {
        startedAt: "2026-04-25T00:00:00Z",
        endedAt: "2026-04-25T00:00:01Z",
        latencyMs: 1000,
      },
      usage: {
        tokensIn: 10,
        tokensOut: 5,
        estimatedCostUsd: 0.01,
      },
      feedbackRefs: [feedback],
      links: {
        scenarioId: "grid_ctf" as Scenario,
        runId: "run-1",
        evalExampleIds: ["eval-1"],
        trainingRecordIds: ["train-1"],
      },
      redactions: [
        {
          path: "messages[0].content",
          reason: "pii-name",
          detectedBy: "operator",
          detectedAt: "2026-04-25T00:00:03Z",
        },
      ],
      routing: {
        chosen: {
          provider: "anthropic",
          model: "claude-sonnet",
          endpoint: "https://api.anthropic.com",
        },
        matchedRouteId: "route-1",
        reason: "matched-route",
        evaluatedAt: "2026-04-25T00:00:04Z",
      },
      metadata: { run: "r1" },
    };

    expect(trace.schemaVersion).toBe("1.0");
    expect(trace.source.sdk.name).toBe("autoctx");
    expect(trace.messages[0]?.toolCalls?.[0]?.toolName).toBe("kb.search");
    expect(trace.feedbackRefs[0]?.ref).toBe("feedback-1");
    expect(trace.routing?.reason).toBe("matched-route");
  });
});
