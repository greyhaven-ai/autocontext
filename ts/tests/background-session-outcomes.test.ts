import { describe, expect, it } from "vitest";

import {
  buildMissingHostCapabilityOutcome,
  buildSessionOutcome,
  buildSessionOutcomeArtifactEvent,
  sessionOutcomeToArtifact,
} from "../src/session/background-session-outcomes.js";

const sessionId = "run:run-123:runtime";
const createdAt = "2026-06-01T00:08:00.000Z";

describe("background session outcome artifacts", () => {
  it("serializes portable outcome artifact kinds without provider-only payloads", () => {
    const outcomes = [
      buildSessionOutcome({
        sessionId,
        kind: "branch",
        title: "Feature branch",
        ref: "feature/ac-785-outcomes",
        url: "https://git.example/compare/feature/ac-785-outcomes",
        createdAt,
        metadata: { base: "main", token: "SECRET_VALUE" },
      }),
      buildSessionOutcome({
        sessionId,
        kind: "commit",
        title: "Implementation commit",
        sha: "abc1234",
        url: "https://git.example/commit/abc1234",
        createdAt,
      }),
      buildSessionOutcome({
        sessionId,
        kind: "pull_request",
        title: "Review PR",
        ref: "42",
        url: "https://git.example/pull/42",
        createdAt,
        metadata: { provider: "github", installation_token: "SECRET_VALUE" },
      }),
      buildSessionOutcome({
        sessionId,
        kind: "screenshot",
        title: "Cockpit screenshot",
        path: "artifacts/cockpit.png",
        createdAt,
      }),
      buildSessionOutcome({
        sessionId,
        kind: "report",
        title: "Session report",
        path: "reports/session.md",
        summary: "Operator-facing run summary",
        createdAt,
      }),
      buildSessionOutcome({
        sessionId,
        kind: "trace",
        title: "Execution trace",
        path: "traces/run.jsonl",
        createdAt,
      }),
      buildSessionOutcome({
        sessionId,
        kind: "dataset",
        title: "Failure examples",
        path: "datasets/failures.jsonl",
        createdAt,
      }),
      buildSessionOutcome({
        sessionId,
        kind: "verification_result",
        title: "Verification result",
        path: "verification/result.json",
        createdAt,
        metadata: { passed: true, failures: 0 },
      }),
    ];

    expect(outcomes).toEqual([
      {
        outcome_id: "branch:feature%2Fac-785-outcomes",
        session_id: sessionId,
        kind: "branch",
        status: "available",
        title: "Feature branch",
        created_at: createdAt,
        url: "https://git.example/compare/feature/ac-785-outcomes",
        path: "",
        ref: "feature/ac-785-outcomes",
        sha: "",
        summary: "",
        metadata: { base: "main" },
      },
      {
        outcome_id: "commit:abc1234",
        session_id: sessionId,
        kind: "commit",
        status: "available",
        title: "Implementation commit",
        created_at: createdAt,
        url: "https://git.example/commit/abc1234",
        path: "",
        ref: "",
        sha: "abc1234",
        summary: "",
        metadata: {},
      },
      {
        outcome_id: "pull_request:42",
        session_id: sessionId,
        kind: "pull_request",
        status: "available",
        title: "Review PR",
        created_at: createdAt,
        url: "https://git.example/pull/42",
        path: "",
        ref: "42",
        sha: "",
        summary: "",
        metadata: { provider: "github" },
      },
      {
        outcome_id: "screenshot:artifacts%2Fcockpit.png",
        session_id: sessionId,
        kind: "screenshot",
        status: "available",
        title: "Cockpit screenshot",
        created_at: createdAt,
        url: "",
        path: "artifacts/cockpit.png",
        ref: "",
        sha: "",
        summary: "",
        metadata: {},
      },
      {
        outcome_id: "report:reports%2Fsession.md",
        session_id: sessionId,
        kind: "report",
        status: "available",
        title: "Session report",
        created_at: createdAt,
        url: "",
        path: "reports/session.md",
        ref: "",
        sha: "",
        summary: "Operator-facing run summary",
        metadata: {},
      },
      {
        outcome_id: "trace:traces%2Frun.jsonl",
        session_id: sessionId,
        kind: "trace",
        status: "available",
        title: "Execution trace",
        created_at: createdAt,
        url: "",
        path: "traces/run.jsonl",
        ref: "",
        sha: "",
        summary: "",
        metadata: {},
      },
      {
        outcome_id: "dataset:datasets%2Ffailures.jsonl",
        session_id: sessionId,
        kind: "dataset",
        status: "available",
        title: "Failure examples",
        created_at: createdAt,
        url: "",
        path: "datasets/failures.jsonl",
        ref: "",
        sha: "",
        summary: "",
        metadata: {},
      },
      {
        outcome_id: "verification_result:verification%2Fresult.json",
        session_id: sessionId,
        kind: "verification_result",
        status: "available",
        title: "Verification result",
        created_at: createdAt,
        url: "",
        path: "verification/result.json",
        ref: "",
        sha: "",
        summary: "",
        metadata: { failures: 0, passed: true },
      },
    ]);
    expect(JSON.stringify(outcomes)).not.toContain("SECRET_VALUE");
  });

  it("represents missing hosted capabilities instead of creating provider-specific outcomes", () => {
    const unavailable = buildMissingHostCapabilityOutcome({
      sessionId,
      kind: "pull_request",
      requiredCapability: "hosted_pull_request_creation",
      createdAt,
    });

    expect(unavailable).toEqual({
      outcome_id: "pull_request:missing:hosted_pull_request_creation",
      session_id: sessionId,
      kind: "pull_request",
      status: "unavailable",
      title: "Pull request unavailable",
      created_at: createdAt,
      url: "",
      path: "",
      ref: "",
      sha: "",
      summary:
        "Host capability hosted_pull_request_creation is unavailable for pull_request outcomes.",
      metadata: {
        reason: "missing_host_capability",
        required_capability: "hosted_pull_request_creation",
      },
    });
    expect(() => sessionOutcomeToArtifact(unavailable)).toThrow(
      "Only available session outcomes can be converted to artifacts",
    );
    expect(() =>
      buildSessionOutcomeArtifactEvent(unavailable, {
        sequence: 71,
        timestamp: "2026-06-01T00:09:10.000Z",
      }),
    ).toThrow("Only available session outcomes can be converted to artifact events");
  });

  it("rejects invalid outcome kind and status values from dynamic callers", () => {
    expect(() =>
      buildSessionOutcome({
        sessionId,
        kind: "video" as never,
        title: "Unsupported video",
        createdAt,
      }),
    ).toThrow("Unsupported session outcome kind: video");

    expect(() =>
      buildSessionOutcome({
        sessionId,
        kind: "report",
        status: "failed" as never,
        title: "Invalid status",
        createdAt,
      }),
    ).toThrow("Unsupported session outcome status: failed");

    expect(() =>
      buildMissingHostCapabilityOutcome({
        sessionId,
        kind: "video" as never,
        requiredCapability: "hosted_video_creation",
        createdAt,
      }),
    ).toThrow("Unsupported session outcome kind: video");
  });

  it("converts available outcomes to sanitized artifacts and normalized artifact_created events", () => {
    const report = buildSessionOutcome({
      sessionId,
      kind: "report",
      title: "Session report",
      path: "reports/session.md",
      createdAt,
      metadata: { api_key: "SECRET_VALUE" },
    });

    expect(sessionOutcomeToArtifact(report)).toEqual({
      artifact_id: "report:reports%2Fsession.md",
      kind: "report",
      label: "Session report",
      path: "reports/session.md",
      url: "",
    });

    expect(
      buildSessionOutcomeArtifactEvent(report, {
        sequence: 70,
        timestamp: "2026-06-01T00:09:00.000Z",
      }),
    ).toEqual({
      event_id: "artifact:run:run-123:runtime:report:reports%2Fsession.md:70",
      session_id: sessionId,
      sequence: 70,
      ts: "2026-06-01T00:09:00.000Z",
      event: "artifact_created",
      source_event_type: "artifact",
      status: "completed",
      title: "Artifact created",
      payload_summary: {
        artifact_id: "report:reports%2Fsession.md",
        kind: "report",
        label: "Session report",
        path: "reports/session.md",
      },
    });
  });
});
