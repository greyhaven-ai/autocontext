import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type {
  AgentOutputRow,
  AgentTaskInterface,
  AgentTaskResult,
  AppId,
  ArtifactEditingInterface,
  CreateProductionTraceInputs,
  ContextBudgetResult,
  ContextBudgetTelemetry,
  EnvironmentTag,
  ExecutionLimits,
  FeedbackRefId,
  GenerationRow,
  HumanFeedbackRow,
  InvestigationInterface,
  LegalAction,
  MatchRow,
  NegotiationInterface,
  CoordinationInterface,
  Observation,
  OperatorLoopInterface,
  ProductionTrace,
  ProductionTraceId,
  RecordMatchOpts,
  ReplayEnvelope,
  Result,
  RunRow,
  Scenario,
  ScenarioInterface,
  SchemaEvolutionInterface,
  ScoringDimension,
  SessionIdHash,
  SimulationInterface,
  TaskQueueRow,
  ToolFragilityInterface,
  TraceSource,
  TrajectoryRow,
  UpsertGenerationOpts,
  UserIdHash,
  WorkflowInterface,
} from "../../packages/ts/core/src/index.ts";
import {
  AgentTaskResultSchema,
  buildPromptBundle,
  CompletionResultSchema,
  ContextBudget,
  ContextBudgetPolicy,
  checkRubricCoherence,
  createProductionTrace,
  ExecutionLimitsSchema,
  estimateTokens,
  expectedScore,
  ObservationSchema,
  ProviderError,
  PRODUCTION_TRACE_SCHEMA_VERSION,
  packageRole,
  packageTopologyVersion,
  parseJudgeResponse,
  ReplayEnvelopeSchema,
  ResultSchema,
  updateElo,
  validateJsonPointer,
  validateProductionTrace,
  validateRetentionPolicy,
  validateRedactionPaths,
  validateTraceSource,
  validateTimingSanity,
} from "../../packages/ts/core/src/index.ts";

const repoRoot = join(import.meta.dirname, "..", "..");

describe("@autocontext/core facade", () => {
  it("preserves the core package identity", () => {
    expect(packageRole).toBe("core");
    expect(packageTopologyVersion).toBe(1);
  });

  it("re-exports production trace contracts from the handwritten contract surface", () => {
    const facadeSource = readFileSync(
      join(repoRoot, "packages", "ts", "core", "src", "index.ts"),
      "utf-8",
    );
    const typeExports = [
      ...facadeSource.matchAll(/export type \{([\s\S]*?)\} from "([^"]+)";/g),
    ].map((match) => ({
      specifiers: match[1]?.match(/\b[A-Za-z][A-Za-z0-9_]*\b/g) ?? [],
      source: match[2],
    }));
    const sourceFor = (specifier: string) =>
      typeExports.find((entry) => entry.specifiers.includes(specifier))?.source;

    expect(PRODUCTION_TRACE_SCHEMA_VERSION).toBe("1.0");
    expect(sourceFor("ProductionTrace")).toBe(
      "../../../../ts/src/production-traces/contract/types.js",
    );
    expect(sourceFor("ProductionOutcome")).toBe(
      "../../../../ts/src/production-traces/contract/types.js",
    );

    const traceSource: TraceSource = {
      emitter: "gateway",
      sdk: { name: "autoctx", version: "0.1.0" },
    };
    const trace: ProductionTrace = {
      schemaVersion: PRODUCTION_TRACE_SCHEMA_VERSION,
      traceId: "01ARZ3NDEKTSV4RRFFQ69G5FAV" as ProductionTraceId,
      source: traceSource,
      provider: { name: "anthropic" },
      model: "claude-sonnet",
      session: {
        userIdHash: "a".repeat(64) as UserIdHash,
        sessionIdHash: "b".repeat(64) as SessionIdHash,
      },
      env: {
        environmentTag: "production" as EnvironmentTag,
        appId: "support-bot" as AppId,
      },
      messages: [
        {
          role: "user",
          content: "help me with a refund",
          timestamp: "2026-04-25T00:00:00Z",
        },
      ],
      toolCalls: [],
      timing: {
        startedAt: "2026-04-25T00:00:00Z",
        endedAt: "2026-04-25T00:00:01Z",
        latencyMs: 1000,
      },
      usage: {
        tokensIn: 10,
        tokensOut: 5,
      },
      feedbackRefs: [
        {
          kind: "rating",
          submittedAt: "2026-04-25T00:00:02Z",
          ref: "feedback-1" as FeedbackRefId,
        },
      ],
      links: {
        scenarioId: "grid_ctf" as Scenario,
      },
      redactions: [],
    };

    expect(trace.source).toBe(traceSource);
    expect(trace.traceId).toBe("01ARZ3NDEKTSV4RRFFQ69G5FAV");
  });

  it("re-exports production trace pure contract helpers", () => {
    const inputs: CreateProductionTraceInputs = {
      id: "01ARZ3NDEKTSV4RRFFQ69G5FAV" as ProductionTraceId,
      source: {
        emitter: "gateway",
        sdk: { name: "autoctx", version: "0.1.0" },
      },
      provider: { name: "anthropic" },
      model: "claude-sonnet",
      env: {
        environmentTag: "production" as EnvironmentTag,
        appId: "support-bot" as AppId,
      },
      messages: [
        {
          role: "user",
          content: "help me with a refund",
          timestamp: "2026-04-25T00:00:00Z",
        },
      ],
      timing: {
        startedAt: "2026-04-25T00:00:00Z",
        endedAt: "2026-04-25T00:00:01Z",
        latencyMs: 1000,
      },
      usage: {
        tokensIn: 10,
        tokensOut: 5,
      },
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-name",
          detectedBy: "operator",
          detectedAt: "2026-04-25T00:00:02Z",
        },
      ],
    };
    const trace = createProductionTrace(inputs);

    expect(trace.schemaVersion).toBe(PRODUCTION_TRACE_SCHEMA_VERSION);
    expect(trace.toolCalls).toEqual([]);
    expect(trace.feedbackRefs).toEqual([]);
    expect(validateTimingSanity(trace.timing).valid).toBe(true);
    expect(validateJsonPointer(trace, "/messages/0/content").valid).toBe(true);
    expect(validateJsonPointer({ "bad~": true }, "/bad~").valid).toBe(false);
    expect(validateRedactionPaths(trace).valid).toBe(true);
  });

  it("re-exports production trace schema validators", () => {
    const trace = createProductionTrace({
      id: "01ARZ3NDEKTSV4RRFFQ69G5FAV" as ProductionTraceId,
      source: {
        emitter: "gateway",
        sdk: { name: "autoctx", version: "0.1.0" },
      },
      provider: { name: "anthropic" },
      model: "claude-sonnet",
      env: {
        environmentTag: "production" as EnvironmentTag,
        appId: "support-bot" as AppId,
      },
      messages: [
        {
          role: "user",
          content: "help me with a refund",
          timestamp: "2026-04-25T00:00:00Z",
        },
      ],
      timing: {
        startedAt: "2026-04-25T00:00:00Z",
        endedAt: "2026-04-25T00:00:01Z",
        latencyMs: 1000,
      },
      usage: {
        tokensIn: 10,
        tokensOut: 5,
      },
    });

    expect(validateProductionTrace(trace).valid).toBe(true);
    expect(
      validateProductionTrace({ ...trace, schemaVersion: "2.0" }).valid,
    ).toBe(false);
    expect(validateTraceSource(trace.source).valid).toBe(true);
    expect(
      validateRetentionPolicy({
        schemaVersion: "1.0",
        retentionDays: 30,
        preserveAll: false,
        preserveCategories: [],
        gcBatchSize: 100,
      }).valid,
    ).toBe(true);
  });

  it("re-exports Elo primitives from the core-safe execution surface", () => {
    expect(expectedScore(1500, 1500)).toBe(0.5);
    expect(updateElo(1500, 1500, 1)).toBe(1512);
  });

  it("re-exports prompt context budget helpers", () => {
    expect(estimateTokens("abcdabcd")).toBe(2);

    const budget = new ContextBudget(20, new ContextBudgetPolicy({ componentTokenCaps: {} }));
    const telemetryResult: ContextBudgetResult = budget.applyWithTelemetry({
      playbook: "12345678901234567890".repeat(20),
      hints: "keep-me",
    });
    const telemetry: ContextBudgetTelemetry = telemetryResult.telemetry;
    const result = telemetryResult.components;

    expect(result.hints).toBe("keep-me");
    expect(result.playbook).toContain("truncated for context budget");
    expect(telemetry.tokenReduction).toBeGreaterThan(0);
  });

  it("re-exports prompt bundle assembly", () => {
    const bundle = buildPromptBundle({
      scenarioRules: "Follow the rules.",
      strategyInterface: "Return JSON.",
      evaluationCriteria: "Maximize score.",
      playbook: "",
      trajectory: "",
      lessons: "",
      tools: "",
      hints: "",
      analysis: "",
    });

    expect(bundle.competitor).toContain("## Scenario Rules");
    expect(bundle.analyst).toContain("## Findings");
    expect(bundle.coach).toContain("<!-- PLAYBOOK_START -->");
    expect(bundle.architect).toContain('"tools"');
  });

  it("re-exports core provider and completion types", () => {
    const parsed = CompletionResultSchema.parse({
      text: "done",
      model: "test-model",
      usage: { input_tokens: 3 },
      costUsd: 0.01,
    });

    expect(parsed.text).toBe("done");
    expect(new ProviderError("boom")).toBeInstanceOf(Error);
  });

  it("re-exports judge parsing and rubric coherence helpers", () => {
    const parsed = parseJudgeResponse(
      '<!-- JUDGE_RESULT_START -->{"score":0.85,"reasoning":"solid","dimensions":{"accuracy":0.9}}<!-- JUDGE_RESULT_END -->',
    );
    const coherence = checkRubricCoherence(
      "Write a brief but comprehensive and concise explanation.",
    );

    expect(parsed.score).toBe(0.85);
    expect(parsed.dimensionScores.accuracy).toBe(0.9);
    expect(coherence.isCoherent).toBe(false);
    expect(coherence.warnings[0]).toContain("contradictory");
  });

  it("re-exports scenario value schemas and types", () => {
    const observation: Observation = ObservationSchema.parse({
      narrative: "Observe",
      state: { board: "ready" },
      constraints: ["no network"],
    });
    const result: Result = ResultSchema.parse({
      score: 0.8,
      summary: "solid",
      validationErrors: [],
    });
    const replay: ReplayEnvelope = ReplayEnvelopeSchema.parse({
      scenario: "grid_ctf",
      seed: 7,
      narrative: "turn-by-turn",
    });
    const limits: ExecutionLimits = ExecutionLimitsSchema.parse({
      timeoutSeconds: 30,
      maxMemoryMb: 1024,
      networkAccess: false,
    });

    expect(observation.state.board).toBe("ready");
    expect(result.passedValidation).toBe(true);
    expect(replay.seed).toBe(7);
    expect(limits.maxMemoryMb).toBe(1024);
  });

  it("re-exports scenario contract interfaces", () => {
    const observation: Observation = ObservationSchema.parse({
      narrative: "Observe",
      state: { board: "ready" },
      constraints: [],
    });
    const result: Result = ResultSchema.parse({
      score: 0.8,
      summary: "solid",
      validationErrors: [],
    });
    const dimension: ScoringDimension = {
      name: "accuracy",
      weight: 0.7,
      description: "Reward accurate play",
    };
    const action: LegalAction = {
      action: "hold",
      description: "Keep current position",
      range: [0, 1],
    };
    const scenario: ScenarioInterface = {
      name: "demo",
      describeRules: () => "rules",
      describeStrategyInterface: () => "return json",
      describeEvaluationCriteria: () => "maximize score",
      initialState: (seed?: number) => ({ seed }),
      getObservation: () => observation,
      validateActions: () => [true, ""],
      step: (state: Record<string, unknown>, actions: Record<string, unknown>) => ({
        ...state,
        ...actions,
        terminal: true,
      }),
      isTerminal: () => true,
      getResult: () => result,
      replayToNarrative: (replay: Array<Record<string, unknown>>) => `${replay.length} events`,
      renderFrame: (state: Record<string, unknown>) => state,
      enumerateLegalActions: () => [action],
      scoringDimensions: () => [dimension],
      executeMatch: () => result,
    };

    expect(scenario.describeRules()).toBe("rules");
    expect(scenario.enumerateLegalActions({})?.[0]?.action).toBe("hold");
    expect(scenario.scoringDimensions()?.[0]?.name).toBe("accuracy");
  });

  it("re-exports agent-task family contracts", async () => {
    const evaluation: AgentTaskResult = AgentTaskResultSchema.parse({
      score: 0.8,
      reasoning: "accepted",
      dimensionScores: { accuracy: 0.9 },
      internalRetries: 1,
    });
    const task: AgentTaskInterface = {
      getTaskPrompt: (state: Record<string, unknown>) =>
        `solve ${String(state.topic ?? "unknown")}`,
      evaluateOutput: async () => evaluation,
      getRubric: () => "be accurate",
      initialState: (seed?: number) => ({ seed, topic: "grid_ctf" }),
      describeTask: () => "demo task",
      prepareContext: async (state: Record<string, unknown>) => ({
        ...state,
        prepared: true,
      }),
      validateContext: () => [],
      reviseOutput: async (output: string) => output,
      verifyFacts: async () => ({ verified: true, issues: [] }),
    };

    expect(task.getTaskPrompt(task.initialState())).toBe("solve grid_ctf");
    expect((await task.evaluateOutput("answer", task.initialState())).score).toBe(0.8);
    expect(await task.prepareContext?.({ topic: "grid_ctf" })).toMatchObject({
      prepared: true,
    });
    expect(await task.verifyFacts?.("answer", task.initialState())).toEqual({
      verified: true,
      issues: [],
    });
  });

  it("re-exports artifact-editing family contracts", () => {
    const scenario: ArtifactEditingInterface = {
      describeTask: () => "edit files",
      getRubric: () => "be correct",
      initialArtifacts: () => [{ path: "README.md", content: "old" }],
      getEditPrompt: (artifacts: unknown[]) => `edit ${artifacts.length} files`,
      validateArtifact: (artifact: unknown) => ({ valid: true, artifact }),
      evaluateEdits: (original: unknown[], edited: unknown[]) => ({
        score: 0.8,
        modified: edited.length - original.length,
      }),
    };

    expect(scenario.describeTask()).toBe("edit files");
    expect(scenario.getEditPrompt(scenario.initialArtifacts())).toBe("edit 1 files");
    expect((scenario.evaluateEdits([], [{}]) as { score: number }).score).toBe(0.8);
  });

  it("re-exports simulation family contracts", () => {
    const simulation: SimulationInterface = {
      describeScenario: () => "demo simulation",
      describeEnvironment: () => ({ name: "demo-sim" }),
      initialState: (seed?: number) => ({ seed, step: 0 }),
      getAvailableActions: () => [{ name: "inspect" }],
      executeAction: (state: Record<string, unknown>, action: unknown) => [
        { success: true, action },
        { ...state, terminal: true },
      ],
      isTerminal: (state: Record<string, unknown>) => Boolean(state.terminal),
      evaluateTrace: (trace: unknown, finalState: Record<string, unknown>) => ({
        trace,
        finalState,
        score: 1,
      }),
      getRubric: () => "finish safely",
    };

    expect(simulation.describeScenario()).toBe("demo simulation");
    expect(simulation.getAvailableActions({})[0]).toMatchObject({
      name: "inspect",
    });
    expect(simulation.executeAction({ step: 0 }, { name: "inspect" })[1]).toMatchObject({
      terminal: true,
    });
  });

  it("re-exports negotiation simulation subfamily contracts", () => {
    const negotiation: NegotiationInterface = {
      describeScenario: () => "demo negotiation",
      describeEnvironment: () => ({ name: "demo-negotiation" }),
      initialState: (seed?: number) => ({ seed, round: 1 }),
      getAvailableActions: () => [{ name: "offer" }],
      executeAction: (state: Record<string, unknown>, action: unknown) => [
        { accepted: false, action },
        { ...state, terminal: true },
      ],
      isTerminal: (state: Record<string, unknown>) => Boolean(state.terminal),
      evaluateTrace: (trace: unknown, finalState: Record<string, unknown>) => ({
        trace,
        finalState,
        score: 1,
      }),
      getRubric: () => "reach a deal",
      getHiddenPreferences: () => ({ reservationValue: 0.4 }),
      getRounds: () => [{ roundNumber: 1 }],
      getOpponentModel: () => ({ confidence: 0.8 }),
      updateOpponentModel: (state: Record<string, unknown>, model: unknown) => ({
        ...state,
        model,
      }),
      evaluateNegotiation: () => ({ score: 0.85 }),
    };

    expect(negotiation.describeScenario()).toBe("demo negotiation");
    expect(negotiation.getRounds({})[0]).toMatchObject({ roundNumber: 1 });
    expect(negotiation.getOpponentModel({})).toMatchObject({ confidence: 0.8 });
    expect(negotiation.updateOpponentModel({ seed: 7 }, { confidence: 0.9 })).toMatchObject({
      model: { confidence: 0.9 },
    });
  });

  it("re-exports investigation simulation subfamily contracts", () => {
    const investigation: InvestigationInterface = {
      describeScenario: () => "demo investigation",
      describeEnvironment: () => ({ name: "demo-investigation" }),
      initialState: (seed?: number) => ({ seed, collected: 0 }),
      getAvailableActions: () => [{ name: "inspect" }],
      executeAction: (state: Record<string, unknown>, action: unknown) => [
        { gathered: true, action },
        { ...state, terminal: true },
      ],
      isTerminal: (state: Record<string, unknown>) => Boolean(state.terminal),
      evaluateTrace: (trace: unknown, finalState: Record<string, unknown>) => ({
        trace,
        finalState,
        score: 1,
      }),
      getRubric: () => "identify root cause",
      getEvidencePool: () => [{ id: "e-1" }],
      evaluateEvidenceChain: () => 0.95,
      evaluateDiagnosis: () => ({ diagnosisCorrect: true }),
    };

    expect(investigation.describeScenario()).toBe("demo investigation");
    expect(investigation.getEvidencePool({})[0]).toMatchObject({ id: "e-1" });
    expect(investigation.evaluateEvidenceChain({}, {})).toBe(0.95);
    expect(investigation.evaluateDiagnosis("root cause", {}, {})).toMatchObject({
      diagnosisCorrect: true,
    });
  });

  it("re-exports workflow simulation subfamily contracts", () => {
    const workflow: WorkflowInterface = {
      describeScenario: () => "demo workflow",
      describeEnvironment: () => ({ name: "demo-workflow" }),
      initialState: (seed?: number) => ({ seed, completed: 0 }),
      getAvailableActions: () => [{ name: "submit" }],
      executeAction: (state: Record<string, unknown>, action: unknown) => [
        { completed: true, action },
        { ...state, terminal: true },
      ],
      isTerminal: (state: Record<string, unknown>) => Boolean(state.terminal),
      evaluateTrace: (trace: unknown, finalState: Record<string, unknown>) => ({
        trace,
        finalState,
        score: 1,
      }),
      getRubric: () => "complete all steps",
      getWorkflowSteps: () => [{ name: "charge-card" }],
      executeStep: () => ({ success: true }),
      executeCompensation: () => ({ success: true }),
      getSideEffects: () => [{ effectType: "payment" }],
      evaluateWorkflow: () => ({ sideEffectsReversed: 1 }),
    };

    expect(workflow.describeScenario()).toBe("demo workflow");
    expect(workflow.getWorkflowSteps()[0]).toMatchObject({
      name: "charge-card",
    });
    expect(workflow.getSideEffects({})[0]).toMatchObject({
      effectType: "payment",
    });
    expect(workflow.evaluateWorkflow({})).toMatchObject({
      sideEffectsReversed: 1,
    });
  });

  it("re-exports schema-evolution simulation subfamily contracts", () => {
    const schemaEvolution: SchemaEvolutionInterface = {
      describeScenario: () => "demo schema evolution",
      describeEnvironment: () => ({ name: "demo-schema-evolution" }),
      initialState: (seed?: number) => ({ seed, schemaVersion: 1 }),
      getAvailableActions: () => [{ name: "migrate" }],
      executeAction: (state: Record<string, unknown>, action: unknown) => [
        { applied: true, action },
        { ...state, schemaVersion: 2 },
      ],
      isTerminal: (state: Record<string, unknown>) => Boolean(state.terminal),
      evaluateTrace: (trace: unknown, finalState: Record<string, unknown>) => ({
        trace,
        finalState,
        score: 1,
      }),
      getRubric: () => "adapt without stale assumptions",
      getMutations: () => [{ version: 2 }],
      getSchemaVersion: (state: Record<string, unknown>) =>
        typeof state.schemaVersion === "number" ? state.schemaVersion : 1,
      getMutationLog: () => [{ version: 2 }],
      applyMutation: (state: Record<string, unknown>, mutation: unknown) => ({
        ...state,
        mutation,
      }),
      checkContextValidity: () => [{ stillValid: false }],
      evaluateAdaptation: () => ({ staleAssumptionsDetected: 1 }),
    };

    expect(schemaEvolution.describeScenario()).toBe("demo schema evolution");
    expect(schemaEvolution.getMutations()[0]).toMatchObject({ version: 2 });
    expect(schemaEvolution.getSchemaVersion({ schemaVersion: 2 })).toBe(2);
    expect(schemaEvolution.checkContextValidity({}, ["customer_id still exists"])).toMatchObject([
      { stillValid: false },
    ]);
  });

  it("re-exports tool-fragility simulation subfamily contracts", () => {
    const toolFragility: ToolFragilityInterface = {
      describeScenario: () => "demo tool fragility",
      describeEnvironment: () => ({ name: "demo-tool-fragility" }),
      initialState: (seed?: number) => ({ seed, toolVersion: 1 }),
      getAvailableActions: () => [{ name: "invoke" }],
      executeAction: (state: Record<string, unknown>, action: unknown) => [
        { invoked: true, action },
        { ...state, toolVersion: 2 },
      ],
      isTerminal: (state: Record<string, unknown>) => Boolean(state.terminal),
      evaluateTrace: (trace: unknown, finalState: Record<string, unknown>) => ({
        trace,
        finalState,
        score: 1,
      }),
      getRubric: () => "adapt after tool drift",
      getToolContracts: () => [{ toolName: "ledger.lookup", version: 1 }],
      getDriftLog: () => [{ toolName: "ledger.lookup", breaking: true }],
      injectDrift: (state: Record<string, unknown>, drift: unknown) => ({
        ...state,
        drift,
      }),
      attributeFailure: () => ({ failureClass: "tool_failure" }),
      evaluateFragility: () => ({ driftsDetected: 1 }),
    };

    expect(toolFragility.describeScenario()).toBe("demo tool fragility");
    expect(toolFragility.getToolContracts({})[0]).toMatchObject({
      toolName: "ledger.lookup",
    });
    expect(toolFragility.getDriftLog({})[0]).toMatchObject({
      breaking: true,
    });
    expect(toolFragility.attributeFailure({}, 1, "missing customer_id")).toMatchObject({
      failureClass: "tool_failure",
    });
  });

  it("re-exports operator-loop simulation subfamily contracts", () => {
    const operatorLoop: OperatorLoopInterface = {
      describeScenario: () => "demo operator loop",
      describeEnvironment: () => ({ name: "demo-operator-loop" }),
      initialState: (seed?: number) => ({
        seed,
        escalations: 0,
        clarifications: 0,
      }),
      getAvailableActions: () => [{ name: "approve" }],
      executeAction: (state: Record<string, unknown>, action: unknown) => [
        { decided: true, action },
        { ...state, terminal: true },
      ],
      isTerminal: (state: Record<string, unknown>) => Boolean(state.terminal),
      evaluateTrace: (trace: unknown, finalState: Record<string, unknown>) => ({
        trace,
        finalState,
        score: 1,
      }),
      getRubric: () => "escalate only when necessary",
      getEscalationLog: () => [{ severity: "critical" }],
      getClarificationLog: () => [{ urgency: "high" }],
      escalate: (state: Record<string, unknown>, event: unknown) => ({
        ...state,
        event,
        escalations: 1,
      }),
      requestClarification: (state: Record<string, unknown>, request: unknown) => ({
        ...state,
        request,
        clarifications: 1,
      }),
      evaluateJudgment: () => ({
        necessaryEscalations: 1,
        clarificationsRequested: 1,
      }),
    };

    expect(operatorLoop.describeScenario()).toBe("demo operator loop");
    expect(operatorLoop.getEscalationLog({})[0]).toMatchObject({
      severity: "critical",
    });
    expect(operatorLoop.getClarificationLog({})[0]).toMatchObject({
      urgency: "high",
    });
    expect(operatorLoop.evaluateJudgment({})).toMatchObject({
      necessaryEscalations: 1,
      clarificationsRequested: 1,
    });
  });

  it("re-exports coordination simulation subfamily contracts", () => {
    const coordination: CoordinationInterface = {
      describeScenario: () => "demo coordination",
      describeEnvironment: () => ({ name: "demo-coordination" }),
      initialState: (seed?: number) => ({ seed, handoffs: 0, merged: false }),
      getAvailableActions: () => [{ name: "merge" }],
      executeAction: (state: Record<string, unknown>, action: unknown) => [
        { merged: true, action },
        { ...state, terminal: true },
      ],
      isTerminal: (state: Record<string, unknown>) => Boolean(state.terminal),
      evaluateTrace: (trace: unknown, finalState: Record<string, unknown>) => ({
        trace,
        finalState,
        score: 1,
      }),
      getRubric: () => "handoff cleanly and merge outputs",
      getWorkerContexts: () => [{ workerId: "worker-a", role: "researcher" }],
      getHandoffLog: () => [{ fromWorker: "worker-a", toWorker: "worker-b" }],
      recordHandoff: (state: Record<string, unknown>, handoff: unknown) => ({
        ...state,
        handoff,
        handoffs: 1,
      }),
      mergeOutputs: (state: Record<string, unknown>, workerOutputs: Record<string, string>) => ({
        ...state,
        workerOutputs,
        merged: true,
      }),
      evaluateCoordination: () => ({ workersUsed: 2, mergeConflicts: 0 }),
    };

    expect(coordination.describeScenario()).toBe("demo coordination");
    expect(coordination.getWorkerContexts({})[0]).toMatchObject({
      workerId: "worker-a",
    });
    expect(coordination.getHandoffLog({})[0]).toMatchObject({
      fromWorker: "worker-a",
      toWorker: "worker-b",
    });
    expect(coordination.evaluateCoordination({})).toMatchObject({
      workersUsed: 2,
      mergeConflicts: 0,
    });
  });

  it("re-exports storage row contracts", () => {
    const run: RunRow = {
      run_id: "run-1",
      scenario: "grid_ctf",
      target_generations: 3,
      executor_mode: "local",
      status: "running",
      agent_provider: "deterministic",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:01Z",
    };
    const generation: GenerationRow = {
      run_id: "run-1",
      generation_index: 0,
      mean_score: 0.75,
      best_score: 0.8,
      elo: 1512,
      wins: 2,
      losses: 1,
      gate_decision: "promote",
      status: "completed",
      duration_seconds: 12,
      dimension_summary_json: '{"accuracy":0.9}',
      scoring_backend: "elo",
      rating_uncertainty: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:01Z",
    };
    const match: MatchRow = {
      id: 1,
      run_id: "run-1",
      generation_index: 0,
      seed: 7,
      score: 0.75,
      passed_validation: 1,
      validation_errors: "",
      winner: "candidate",
      strategy_json: "{}",
      replay_json: "{}",
      created_at: "2026-01-01T00:00:02Z",
    };
    const output: AgentOutputRow = {
      id: 2,
      run_id: "run-1",
      generation_index: 0,
      role: "competitor",
      content: "answer",
      created_at: "2026-01-01T00:00:03Z",
    };
    const feedback: HumanFeedbackRow = {
      id: 3,
      scenario_name: "grid_ctf",
      generation_id: "run-1:0",
      agent_output: "answer",
      human_score: 0.8,
      human_notes: "solid",
      created_at: "2026-01-01T00:00:04Z",
    };
    const queue: TaskQueueRow = {
      id: "task-1",
      spec_name: "grid_ctf",
      status: "pending",
      priority: 1,
      config_json: null,
      scheduled_at: null,
      started_at: null,
      completed_at: null,
      best_score: null,
      best_output: null,
      total_rounds: null,
      met_threshold: 0,
      result_json: null,
      error: null,
      created_at: "2026-01-01T00:00:05Z",
      updated_at: "2026-01-01T00:00:06Z",
    };
    const trajectory: TrajectoryRow = {
      generation_index: 0,
      mean_score: 0.75,
      best_score: 0.8,
      elo: 1512,
      gate_decision: "promote",
      delta: 12,
      dimension_summary: { accuracy: 0.9 },
      scoring_backend: "elo",
      rating_uncertainty: null,
    };
    const upsert: UpsertGenerationOpts = {
      meanScore: 0.75,
      bestScore: 0.8,
      elo: 1512,
      wins: 2,
      losses: 1,
      gateDecision: "promote",
      status: "completed",
      durationSeconds: 12,
      dimensionSummaryJson: '{"accuracy":0.9}',
      scoringBackend: "elo",
      ratingUncertainty: null,
    };
    const recordMatch: RecordMatchOpts = {
      seed: 7,
      score: 0.75,
      passedValidation: true,
      validationErrors: "",
      winner: "candidate",
      strategyJson: "{}",
      replayJson: "{}",
    };

    expect(run.scenario).toBe("grid_ctf");
    expect(generation.elo).toBe(1512);
    expect(match.winner).toBe("candidate");
    expect(output.role).toBe("competitor");
    expect(feedback.human_notes).toBe("solid");
    expect(queue.status).toBe("pending");
    expect(trajectory.delta).toBe(12);
    expect(upsert.gateDecision).toBe("promote");
    expect(recordMatch.passedValidation).toBe(true);
  });
});
