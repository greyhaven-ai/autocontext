import { describe, expect, it } from "vitest";
import type {
	AgentsStartedPayload,
	AppId,
	EnvironmentTag,
	FeedbackRef,
	FeedbackRefId,
	GenerationStartedPayload,
	ProductionTrace,
	ProductionTraceId,
	ProviderInfo,
	ResearchAdapter,
	RoleCompletedPayload,
	RunFailedPayload,
	RunStartedPayload,
	Scenario,
	SessionIdHash,
	StagnationReport,
	TournamentCompletedPayload,
	TournamentStartedPayload,
	TraceSource,
	UserIdHash,
} from "../../packages/ts/control-plane/src/index.ts";
import {
	AckMsgSchema,
	AuthStatusMsgSchema,
	CancelScenarioCmdSchema,
	ChatAgentCmdSchema,
	ChatResponseMsgSchema,
	Citation,
	ConfirmScenarioCmdSchema,
	CreateScenarioCmdSchema,
	EnvironmentsMsgSchema,
	ErrorMsgSchema,
	EventMsgSchema,
	ExecutorInfoSchema,
	ExecutorResourcesSchema,
	HelloMsgSchema,
	InjectHintCmdSchema,
	ListScenariosCmdSchema,
	LoginCmdSchema,
	LogoutCmdSchema,
	MissionProgressMsgSchema,
	MonitorAlertMsgSchema,
	OverrideGateCmdSchema,
	PauseCmdSchema,
	PRODUCTION_TRACE_SCHEMA_VERSION,
	PROTOCOL_VERSION,
	packageRole,
	packageTopologyVersion,
	ResearchBrief,
	ResearchConfig,
	ResearchQuery,
	ResearchResult,
	ResumeCmdSchema,
	ReviseScenarioCmdSchema,
	RunAcceptedMsgSchema,
	ScenarioErrorMsgSchema,
	ScenarioGeneratingMsgSchema,
	ScenarioInfoSchema,
	ScenarioPreviewMsgSchema,
	ScenarioReadyMsgSchema,
	ScoringComponentSchema,
	StartRunCmdSchema,
	StateMsgSchema,
	StrategyParamSchema,
	SwitchProviderCmdSchema,
	Urgency,
	WhoamiCmdSchema,
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

	it("re-exports research brief values", () => {
		const citation = new Citation({
			source: "policy handbook",
			url: "https://example.com/policy",
			relevance: 0.95,
			snippet: "Refunds require manager sign-off after 30 days.",
			retrievedAt: "2026-04-25T00:00:00Z",
		});
		const strongResult = new ResearchResult({
			queryTopic: "refund policy",
			summary: "Manager sign-off required after 30 days.",
			citations: [citation],
			confidence: 0.91,
			metadata: { adapter: "demo" },
		});
		const weakResult = new ResearchResult({
			queryTopic: "escalation policy",
			summary: "Escalate unusual refund cases.",
			citations: [citation],
			confidence: 0.42,
			metadata: { adapter: "demo" },
		});
		const brief = ResearchBrief.fromResults(
			"Summarize refund policy changes",
			[strongResult, weakResult],
			0.9,
		);

		expect(brief.goal).toBe("Summarize refund policy changes");
		expect(brief.findings).toHaveLength(1);
		expect(brief.findings[0]?.queryTopic).toBe("refund policy");
		expect(brief.uniqueCitations).toHaveLength(1);
		expect(brief.uniqueCitations[0]?.source).toBe("policy handbook");
		expect(brief.avgConfidence).toBe(0.91);
		expect(brief.toMarkdown()).toContain(
			"Research Brief: Summarize refund policy changes",
		);
	});

	it("re-exports tournament started payload types", () => {
		const payload: TournamentStartedPayload = {
			run_id: "run-123",
			generation: 2,
			matches: 8,
		};

		expect(payload.run_id).toBe("run-123");
		expect(payload.generation).toBe(2);
		expect(payload.matches).toBe(8);
	});

	it("re-exports tournament completed payload types", () => {
		const payload: TournamentCompletedPayload = {
			run_id: "run-123",
			generation: 2,
			mean_score: 0.55,
			best_score: 0.7,
			wins: 3,
			losses: 1,
		};

		expect(payload.run_id).toBe("run-123");
		expect(payload.generation).toBe(2);
		expect(payload.mean_score).toBe(0.55);
		expect(payload.best_score).toBe(0.7);
		expect(payload.wins).toBe(3);
		expect(payload.losses).toBe(1);
	});

	it("re-exports role completed payload types", () => {
		const payload: RoleCompletedPayload = {
			run_id: "run-123",
			generation: 2,
			role: "coach",
			latency_ms: 125,
			tokens: 42,
		};

		expect(payload.run_id).toBe("run-123");
		expect(payload.generation).toBe(2);
		expect(payload.role).toBe("coach");
		expect(payload.latency_ms).toBe(125);
		expect(payload.tokens).toBe(42);
	});

	it("re-exports run started payload types", () => {
		const payload: RunStartedPayload = {
			run_id: "run-123",
			scenario: "grid_ctf",
			target_generations: 5,
		};

		expect(payload.run_id).toBe("run-123");
		expect(payload.scenario).toBe("grid_ctf");
		expect(payload.target_generations).toBe(5);
	});

	it("re-exports run failed payload types", () => {
		const payload: RunFailedPayload = {
			run_id: "run-123",
			error: "boom",
		};

		expect(payload.run_id).toBe("run-123");
		expect(payload.error).toBe("boom");
	});

	it("re-exports generation kickoff payload types", () => {
		const generationStarted: GenerationStartedPayload = {
			run_id: "run-123",
			generation: 2,
		};
		const agentsStarted: AgentsStartedPayload = {
			run_id: "run-123",
			generation: 2,
			roles: ["competitor", "analyst", "coach", "curator"],
		};

		expect(generationStarted.run_id).toBe("run-123");
		expect(generationStarted.generation).toBe(2);
		expect(agentsStarted.roles).toEqual([
			"competitor",
			"analyst",
			"coach",
			"curator",
		]);
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

	it("re-exports environment discovery messages", () => {
		const environments = EnvironmentsMsgSchema.parse({
			type: "environments",
			scenarios: [
				ScenarioInfoSchema.parse({
					name: "grid_ctf",
					description: "Capture the flag",
				}),
				ScenarioInfoSchema.parse({
					name: "schema_repair",
					description: "Recover a schema from examples.",
				}),
			],
			executors: [
				ExecutorInfoSchema.parse({
					mode: "docker",
					available: true,
					description: "Local Docker executor",
					resources: ExecutorResourcesSchema.parse({
						docker_image: "ghcr.io/greyhaven/executor:latest",
						cpu_cores: 4,
						memory_gb: 8,
						disk_gb: 20,
						timeout_minutes: 15,
					}),
				}),
			],
			current_executor: "docker",
			agent_provider: "pi",
		});

		expect(environments.scenarios[1]?.name).toBe("schema_repair");
		expect(environments.executors[0]?.resources?.cpu_cores).toBe(4);
		expect(environments.current_executor).toBe("docker");
		expect(environments.agent_provider).toBe("pi");
	});

	it("re-exports run acceptance messages", () => {
		const accepted = RunAcceptedMsgSchema.parse({
			type: "run_accepted",
			run_id: "run-123",
			scenario: "schema_repair",
			generations: 4,
		});

		expect(accepted.run_id).toBe("run-123");
		expect(accepted.scenario).toBe("schema_repair");
		expect(accepted.generations).toBe(4);
	});

	it("re-exports chat response messages", () => {
		const response = ChatResponseMsgSchema.parse({
			type: "chat_response",
			role: "assistant",
			text: "Schema looks valid.",
		});

		expect(response.role).toBe("assistant");
		expect(response.text).toBe("Schema looks valid.");
	});

	it("re-exports event messages", () => {
		const event = EventMsgSchema.parse({
			type: "event",
			event: "run_progress",
			payload: { run_id: "run-123", percent: 50 },
		});

		expect(event.event).toBe("run_progress");
		expect(event.payload).toEqual({ run_id: "run-123", percent: 50 });
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

	it("re-exports auth status messages", () => {
		const auth = AuthStatusMsgSchema.parse({
			type: "auth_status",
			provider: "anthropic",
			authenticated: true,
			model: "claude-sonnet",
			configuredProviders: [
				{ provider: "anthropic", hasApiKey: true },
				{ provider: "openai", hasApiKey: false },
			],
		});

		expect(auth.provider).toBe("anthropic");
		expect(auth.authenticated).toBe(true);
		expect(auth.model).toBe("claude-sonnet");
		expect(auth.configuredProviders?.[1]?.hasApiKey).toBe(false);
	});

	it("re-exports mission progress messages", () => {
		const progress = MissionProgressMsgSchema.parse({
			type: "mission_progress",
			missionId: "mission-1",
			status: "running",
			stepsCompleted: 3,
			latestStep: "evaluate candidate",
			budgetUsed: 1.25,
			budgetMax: 5,
		});

		expect(progress.missionId).toBe("mission-1");
		expect(progress.status).toBe("running");
		expect(progress.stepsCompleted).toBe(3);
		expect(progress.latestStep).toBe("evaluate candidate");
		expect(progress.budgetUsed).toBe(1.25);
		expect(progress.budgetMax).toBe(5);
	});

	it("re-exports basic client control commands", () => {
		const pause = PauseCmdSchema.parse({ type: "pause" });
		const resume = ResumeCmdSchema.parse({ type: "resume" });
		const injectHint = InjectHintCmdSchema.parse({
			type: "inject_hint",
			text: "Try broader search.",
		});
		const overrideGate = OverrideGateCmdSchema.parse({
			type: "override_gate",
			decision: "retry",
		});
		const invalidHint = InjectHintCmdSchema.safeParse({
			type: "inject_hint",
			text: "",
		});

		expect(pause.type).toBe("pause");
		expect(resume.type).toBe("resume");
		expect(injectHint.text).toBe("Try broader search.");
		expect(overrideGate.decision).toBe("retry");
		expect(invalidHint.success).toBe(false);
	});

	it("re-exports auth commands", () => {
		const login = LoginCmdSchema.parse({
			type: "login",
			provider: "anthropic",
			apiKey: "test-key",
			model: "claude-sonnet",
			baseUrl: "https://api.anthropic.com",
		});
		const logout = LogoutCmdSchema.parse({
			type: "logout",
			provider: "anthropic",
		});
		const switchProvider = SwitchProviderCmdSchema.parse({
			type: "switch_provider",
			provider: "openai",
		});
		const whoami = WhoamiCmdSchema.parse({ type: "whoami" });
		const invalidLogin = LoginCmdSchema.safeParse({
			type: "login",
			provider: "",
		});
		const invalidSwitch = SwitchProviderCmdSchema.safeParse({
			type: "switch_provider",
			provider: "",
		});

		expect(login.provider).toBe("anthropic");
		expect(login.model).toBe("claude-sonnet");
		expect(logout.provider).toBe("anthropic");
		expect(switchProvider.provider).toBe("openai");
		expect(whoami.type).toBe("whoami");
		expect(invalidLogin.success).toBe(false);
		expect(invalidSwitch.success).toBe(false);
	});

	it("re-exports chat agent command", () => {
		const chatAgent = ChatAgentCmdSchema.parse({
			type: "chat_agent",
			role: "coach",
			message: "Try broader search.",
		});
		const invalidChatAgent = ChatAgentCmdSchema.safeParse({
			type: "chat_agent",
			role: "coach",
			message: "",
		});

		expect(chatAgent.role).toBe("coach");
		expect(chatAgent.message).toBe("Try broader search.");
		expect(invalidChatAgent.success).toBe(false);
	});

	it("re-exports run setup commands", () => {
		const listScenarios = ListScenariosCmdSchema.parse({
			type: "list_scenarios",
		});
		const startRun = StartRunCmdSchema.parse({
			type: "start_run",
			scenario: "schema_repair",
			generations: 3,
		});
		const invalidStartRun = StartRunCmdSchema.safeParse({
			type: "start_run",
			scenario: "schema_repair",
			generations: 0,
		});

		expect(listScenarios.type).toBe("list_scenarios");
		expect(startRun.scenario).toBe("schema_repair");
		expect(startRun.generations).toBe(3);
		expect(invalidStartRun.success).toBe(false);
	});

	it("re-exports scenario authoring commands", () => {
		const create = CreateScenarioCmdSchema.parse({
			type: "create_scenario",
			description: "Design a schema repair scenario.",
		});
		const confirm = ConfirmScenarioCmdSchema.parse({
			type: "confirm_scenario",
		});
		const revise = ReviseScenarioCmdSchema.parse({
			type: "revise_scenario",
			feedback: "Make the failure mode more concrete.",
		});
		const cancel = CancelScenarioCmdSchema.parse({
			type: "cancel_scenario",
		});
		const invalidCreate = CreateScenarioCmdSchema.safeParse({
			type: "create_scenario",
			description: "",
		});

		expect(create.description).toBe("Design a schema repair scenario.");
		expect(confirm.type).toBe("confirm_scenario");
		expect(revise.feedback).toBe("Make the failure mode more concrete.");
		expect(cancel.type).toBe("cancel_scenario");
		expect(invalidCreate.success).toBe(false);
	});

	it("re-exports stagnation report types", () => {
		const report: StagnationReport = {
			isStagnated: true,
			trigger: "score_plateau",
			detail: "score stddev 0.000001 < epsilon 0.01 over last 5 gens",
		};

		expect(report.isStagnated).toBe(true);
		expect(report.trigger).toBe("score_plateau");
		expect(report.detail).toBe(
			"score stddev 0.000001 < epsilon 0.01 over last 5 gens",
		);
	});

	it("re-exports monitor alert messages", () => {
		const alert = MonitorAlertMsgSchema.parse({
			type: "monitor_alert",
			alert_id: "alert-1",
			condition_id: "cond-1",
			condition_name: "stalled-run",
			condition_type: "stall_window",
			scope: "run:run-123",
			detail: "No events for 30.0s (timeout=30.0s)",
		});

		expect(alert.condition_name).toBe("stalled-run");
		expect(alert.detail).toBe("No events for 30.0s (timeout=30.0s)");
	});

	it("requires stage for scenario error messages", () => {
		const parsed = ScenarioErrorMsgSchema.safeParse({
			type: "scenario_error",
			message: "designer failed",
		});

		expect(parsed.success).toBe(false);
	});

	it("re-exports scenario generation lifecycle messages", () => {
		const generating = ScenarioGeneratingMsgSchema.parse({
			type: "scenario_generating",
			name: "schema_repair",
		});
		const preview = ScenarioPreviewMsgSchema.parse({
			type: "scenario_preview",
			name: "schema_repair",
			display_name: "Schema Repair",
			description: "Recover a schema from examples.",
			strategy_params: [
				StrategyParamSchema.parse({
					name: "depth",
					description: "Reasoning depth",
				}),
			],
			scoring_components: [
				ScoringComponentSchema.parse({
					name: "accuracy",
					description: "Schema fidelity",
					weight: 0.8,
				}),
			],
			constraints: ["No external tools"],
			win_threshold: 0.75,
		});
		const ready = ScenarioReadyMsgSchema.parse({
			type: "scenario_ready",
			name: "schema_repair",
			test_scores: [0.8, 0.9],
		});
		const error = ScenarioErrorMsgSchema.parse({
			type: "scenario_error",
			message: "designer failed",
			stage: "preview",
		});

		expect(generating.name).toBe("schema_repair");
		expect(preview.display_name).toBe("Schema Repair");
		expect(preview.strategy_params[0]?.name).toBe("depth");
		expect(preview.scoring_components[0]?.weight).toBe(0.8);
		expect(preview.constraints).toEqual(["No external tools"]);
		expect(preview.win_threshold).toBe(0.75);
		expect(ready.test_scores).toEqual([0.8, 0.9]);
		expect(error.stage).toBe("preview");
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
