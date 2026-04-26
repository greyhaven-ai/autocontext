import { describe, expect, it } from "vitest";
import type {
	AppId,
	EnvironmentTag,
	FeedbackRef,
	FeedbackRefId,
	ProductionTrace,
	ProductionTraceId,
	ProviderInfo,
	Scenario,
	SessionIdHash,
	TraceSource,
	UserIdHash,
} from "../../packages/ts/control-plane/src/index.ts";
import {
	PRODUCTION_TRACE_SCHEMA_VERSION,
	packageRole,
	packageTopologyVersion,
} from "../../packages/ts/control-plane/src/index.ts";

describe("@autocontext/control-plane facade", () => {
	it("preserves the control package identity", () => {
		expect(packageRole).toBe("control");
		expect(packageTopologyVersion).toBe(1);
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
