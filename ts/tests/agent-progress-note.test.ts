import { describe, expect, it } from "vitest";

import {
  AGENT_PROGRESS_NOTE_EVENT_NAME,
  AgentProgressNotePayloadSchema,
  MAX_AGENT_PROGRESS_NOTE_TEXT_LENGTH,
  createAgentProgressNotePublisher,
  isAgentProgressNotePayloadRetainable,
  sanitizeAgentProgressNotePayload,
  sanitizeAgentProgressNoteText,
  type AgentProgressNoteEvidenceTarget,
  type AgentProgressNoteKind,
  type AgentProgressNotePayload,
} from "../src/loop/agent-progress-note.js";
import { sanitizeRunTranscriptMessage } from "../src/server/run-transcript-frame.js";

const NOTE_KINDS: AgentProgressNoteKind[] = [
  "intent",
  "discovery",
  "decision",
  "verification",
  "blocker",
];

function validPayload(overrides: Partial<AgentProgressNotePayload> = {}): AgentProgressNotePayload {
  return {
    run_id: "run-1",
    generation: 1,
    kind: "discovery",
    text: "The retained source confirms the release is reversible.",
    ...overrides,
  };
}

function maximumLengthId(prefix: string, index: number): string {
  const base = `${prefix}-${index}-`;
  return `${base}${"a".repeat(200 - base.length)}`;
}

describe("agent progress note protocol", () => {
  it.each(NOTE_KINDS)("accepts strict %s notes and both evidence variants", (kind) => {
    const payload = validPayload({
      kind,
      evidence_targets: [
        { kind: "action", action_id: "inspect-release" },
        {
          kind: "artifact",
          action_id: "inspect-release",
          artifact_id: "release-report",
        },
      ],
    });

    expect(AgentProgressNotePayloadSchema.parse(payload)).toEqual(payload);
    expect(AgentProgressNotePayloadSchema.safeParse({ ...payload, unexpected: true }).success).toBe(
      false,
    );
    expect(
      AgentProgressNotePayloadSchema.safeParse({
        ...payload,
        evidence_targets: [
          { kind: "action", action_id: "inspect-release", artifact_id: "not-allowed" },
        ],
      }).success,
    ).toBe(false);
  });

  it("enforces copy, generation, ID, count, and collision-safe uniqueness limits", () => {
    expect(
      AgentProgressNotePayloadSchema.safeParse(
        validPayload({ text: "x".repeat(MAX_AGENT_PROGRESS_NOTE_TEXT_LENGTH + 1) }),
      ).success,
    ).toBe(false);
    expect(AgentProgressNotePayloadSchema.safeParse(validPayload({ text: "   " })).success).toBe(
      false,
    );
    expect(AgentProgressNotePayloadSchema.safeParse(validPayload({ generation: -1 })).success).toBe(
      false,
    );
    expect(
      AgentProgressNotePayloadSchema.safeParse({
        ...validPayload(),
        run_id: "run-ghp_abcdefghijklmnopqrstuvwxyz123456",
      }).success,
    ).toBe(false);
    expect(
      AgentProgressNotePayloadSchema.safeParse({
        ...validPayload(),
        evidence_targets: Array.from({ length: 6 }, (_, index) => ({
          kind: "action",
          action_id: `action-${index}`,
        })),
      }).success,
    ).toBe(false);
    expect(
      AgentProgressNotePayloadSchema.safeParse({
        ...validPayload(),
        evidence_targets: [
          { kind: "action", action_id: "action:artifact" },
          { kind: "artifact", action_id: "action", artifact_id: "artifact" },
        ],
      }).success,
    ).toBe(true);
    expect(
      AgentProgressNotePayloadSchema.safeParse({
        ...validPayload(),
        evidence_targets: [
          { kind: "artifact", action_id: "action", artifact_id: "artifact" },
          { kind: "artifact", action_id: "action", artifact_id: "artifact" },
        ],
      }).success,
    ).toBe(false);
  });

  it("redacts credentials, URLs, and selector-shaped fragments before raw emission", () => {
    const rawPayloads: Record<string, unknown>[] = [];
    const publisher = createAgentProgressNotePublisher({
      runId: "run-safe",
      events: {
        emit(event, payload) {
          expect(event).toBe(AGENT_PROGRESS_NOTE_EVENT_NAME);
          rawPayloads.push(payload);
        },
      },
    });

    expect(
      publisher?.publish({
        generation: 2,
        kind: "verification",
        text: "Authorization: Bearer private-value checked https://example.test/report with selector=#submit",
      }),
    ).toBe(true);

    const wire = JSON.stringify(rawPayloads);
    expect(wire).toContain("[Redacted]");
    expect(wire).toContain("[URL omitted]");
    expect(wire).toContain("[Selector omitted]");
    expect(wire).not.toContain("private-value");
    expect(wire).not.toContain("example.test");
    expect(wire).not.toContain("#submit");
    expect(
      sanitizeRunTranscriptMessage({
        type: "event",
        event: AGENT_PROGRESS_NOTE_EVENT_NAME,
        payload: rawPayloads.at(0) ?? {},
      }),
    ).toMatchObject({
      type: "event",
      event: AGENT_PROGRESS_NOTE_EVENT_NAME,
      payload: rawPayloads.at(0),
    });
  });

  it.each([
    ['Authorization: "Bearer private-value"', "private-value"],
    ["Authorization: Basic Zm9vOmJhcg==", "Zm9vOmJhcg=="],
    ["Authorization: Digest username=alice,response=deadbeef", "deadbeef"],
    ["Cookie: session=abc; csrf=secret", "csrf=secret"],
    ["Set-Cookie: session=abc; HttpOnly", "session=abc"],
    ["API key: abcdefghijklmnop", "abcdefghijklmnop"],
    ["OPENAI_API_KEY=ordinary-looking-value", "ordinary-looking-value"],
    ["openai_api_key=lowercase-private-value", "lowercase-private-value"],
    ["OpenAI_API_KEY=mixed-case-private-value", "mixed-case-private-value"],
    ["AWS_SECRET_ACCESS_KEY='plain private value'", "plain private value"],
    ["aws_secret_access_key=lowercase-secret-value", "lowercase-secret-value"],
    ["db_password=ordinary-database-password", "ordinary-database-password"],
    ["openaiApiKey=camel-private-value", "camel-private-value"],
    ["awsSecretAccessKey='camel aws private value'", "camel aws private value"],
    ["oauthClientSecret: camel-oauth-secret", "camel-oauth-secret"],
    ["githubAuthToken=camel-github-token", "camel-github-token"],
    ["stripeRefreshToken=analogous-provider-token", "analogous-provider-token"],
    ['export GITHUB_TOKEN="not-provider-shaped value"', "not-provider-shaped value"],
    ["AWS_ACCESS_KEY_ID=ordinary-access-id", "ordinary-access-id"],
    ["Fetch ftp://example.test/secret", "example.test"],
    ["Email mailto:alice@example.test", "alice@example.test"],
    ["Inspect example.test/path before continuing", "example.test"],
    ["Inspect 10.0.0.5/admin before continuing", "10.0.0.5"],
    ["Inspect localhost:3000/admin before continuing", "localhost:3000"],
    ["Inspect [2001:db8::1]:3000/admin before continuing", "2001:db8::1"],
    ["Inspect 2001:db8::1/admin before continuing", "2001:db8::1"],
    ["Click #submit after validation", "#submit"],
    ["Use button:hover after validation", "button:hover"],
    ["Use input:nth-child(2) after validation", "input:nth-child(2)"],
    ["Inspect div > button.primary", "button.primary"],
    ["Read xpath=//main/button[1]", "//main/button[1]"],
  ])("removes prohibited presentation target from %s", (text, prohibited) => {
    const sanitized = sanitizeAgentProgressNoteText(text);
    expect(sanitized).not.toContain(prohibited);
    expect(sanitized).toMatch(/\[(?:Redacted|Selector omitted|URL omitted)]/);
  });

  it.each([
    "The authorization review completed successfully.",
    "The API key rotation policy passed without exposing a value.",
    "OPENAI_API_KEY rotation completed without exposing a value.",
    "API_KEY_ROTATION_DAYS=30 remains a safe policy setting.",
    "api_key_rotation_days=30 remains a safe policy setting.",
    "openaiApiKey rotation completed without exposing a value.",
    "openaiApiKeyRotationDays=30 remains a safe policy setting.",
    "oauthClientSecretRotationEnabled=true remains safe configuration.",
    "githubAuthTokenStatus=valid remains safe operational metadata.",
    "RESULT_COUNT=4 remains safe operational metadata.",
    "The button remains active while localhost testing continues.",
    "Version 1.2.3.4 improved the retained result.",
  ])("preserves safe prose in %s", (text) => {
    expect(sanitizeAgentProgressNoteText(text)).toBe(text);
  });

  it("removes the complete CSS combinator expression before domain redaction", () => {
    const sanitized = sanitizeAgentProgressNoteText("Inspect div > button.primary");

    expect(sanitized).toBe("Inspect [Selector omitted]");
    expect(sanitized).not.toMatch(/div|>|button|primary/);
  });

  it("re-bounds copy after redaction expands it", () => {
    const raw = Array.from({ length: 60 }, () => "token=x").join(" ");
    expect(raw.length).toBeLessThanOrEqual(MAX_AGENT_PROGRESS_NOTE_TEXT_LENGTH);

    const payload = sanitizeAgentProgressNotePayload(validPayload({ text: raw }));
    expect(payload?.text).not.toContain("token=x");
    expect(payload?.text).toContain("[Redacted]");
    expect(payload?.text.length).toBeLessThanOrEqual(MAX_AGENT_PROGRESS_NOTE_TEXT_LENGTH);
    expect(payload?.text.endsWith("…")).toBe(true);
  });

  it("suppresses semantic duplicates but permits retry after an emitter failure", () => {
    const emissions: AgentProgressNotePayload[] = [];
    let attempts = 0;
    const publisher = createAgentProgressNotePublisher({
      runId: "run-retry",
      events: {
        emit(_event, payload) {
          attempts += 1;
          if (attempts === 1) throw new Error("temporary observer failure");
          emissions.push(AgentProgressNotePayloadSchema.parse(payload));
        },
      },
    });
    const note = {
      generation: 1,
      kind: "intent",
      text: "Inspect the retained run context.",
    } as const;

    expect(publisher?.publish(note)).toBe(false);
    expect(publisher?.publish(note)).toBe(true);
    expect(publisher?.publish(note)).toBe(false);
    expect(attempts).toBe(2);
    expect(emissions).toHaveLength(1);
  });

  it("deduplicates semantically identical evidence regardless of target order", () => {
    const emissions: AgentProgressNotePayload[] = [];
    const publisher = createAgentProgressNotePublisher({
      runId: "run-evidence-order",
      events: {
        emit(_event, payload) {
          emissions.push(AgentProgressNotePayloadSchema.parse(payload));
        },
      },
    });
    const action = { kind: "action", action_id: "inspect-release" } as const;
    const artifact = {
      kind: "artifact",
      action_id: "inspect-release",
      artifact_id: "release-report",
    } as const;
    const note = {
      generation: 1,
      kind: "verification",
      text: "The retained evidence verifies the result.",
    } as const;

    expect(publisher?.publish({ ...note, evidenceTargets: [action, artifact] })).toBe(true);
    expect(publisher?.publish({ ...note, evidenceTargets: [artifact, action] })).toBe(false);
    expect(emissions).toHaveLength(1);
  });

  it("rejects unsafe publisher identity and invalid runtime input without emitting", () => {
    expect(
      createAgentProgressNotePublisher({
        runId: "run-dp_abcdefghijklmnopqrstuvwxyz",
        events: { emit() {} },
      }),
    ).toBeNull();

    const emissions: Record<string, unknown>[] = [];
    const publisher = createAgentProgressNotePublisher({
      runId: "run-valid",
      events: { emit: (_event, payload) => emissions.push(payload) },
    });
    expect(
      publisher?.publish({
        generation: Number.NaN,
        kind: "discovery",
        text: "Invalid generation",
      }),
    ).toBe(false);
    expect(
      publisher?.publish({
        generation: 0,
        kind: "discovery",
        text: "   ",
      }),
    ).toBe(false);
    expect(emissions).toEqual([]);
  });

  it("drops a valid but oversized multibyte note atomically", () => {
    const evidenceTargets: AgentProgressNoteEvidenceTarget[] = Array.from(
      { length: 5 },
      (_, index) => ({
        kind: "artifact",
        action_id: maximumLengthId("action", index),
        artifact_id: maximumLengthId("artifact", index),
      }),
    );
    const oversized = validPayload({
      run_id: maximumLengthId("run", 1),
      text: String.fromCharCode(55_296).repeat(MAX_AGENT_PROGRESS_NOTE_TEXT_LENGTH),
      evidence_targets: evidenceTargets,
    });
    expect(AgentProgressNotePayloadSchema.safeParse(oversized).success).toBe(true);
    expect(isAgentProgressNotePayloadRetainable(oversized)).toBe(false);

    const emissions: Record<string, unknown>[] = [];
    const publisher = createAgentProgressNotePublisher({
      runId: oversized.run_id,
      events: { emit: (_event, payload) => emissions.push(payload) },
    });
    expect(
      publisher?.publish({
        generation: oversized.generation,
        kind: oversized.kind,
        text: oversized.text,
        evidenceTargets,
      }),
    ).toBe(false);
    expect(emissions).toEqual([]);
  });

  it("omits optional evidence from the exact wire payload", () => {
    const emissions: AgentProgressNotePayload[] = [];
    const publisher = createAgentProgressNotePublisher({
      runId: "run-no-evidence",
      events: {
        emit(_event, payload) {
          emissions.push(AgentProgressNotePayloadSchema.parse(payload));
        },
      },
    });
    expect(
      publisher?.publish({
        generation: 0,
        kind: "intent",
        text: "Prepare the run context.",
      }),
    ).toBe(true);
    expect(emissions).toEqual([
      {
        run_id: "run-no-evidence",
        generation: 0,
        kind: "intent",
        text: "Prepare the run context.",
      },
    ]);
  });
});
