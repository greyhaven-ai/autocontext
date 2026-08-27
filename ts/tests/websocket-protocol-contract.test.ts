import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { buildEventStreamEnvelope } from "../src/server/event-stream-envelope.js";
import {
  AGENT_PROGRESS_NOTE_CAPABILITY,
  AckMsgSchema,
  AgentProgressNotePayloadSchema,
  CLIENT_MESSAGE_TYPES,
  ChatAgentCmdSchema,
  ExecutorResourcesSchema,
  MonitorAlertMsgSchema,
  PYTHON_SHARED_CLIENT_MESSAGE_TYPES,
  PYTHON_SHARED_SERVER_MESSAGE_TYPES,
  ScenarioErrorMsgSchema,
  SERVER_MESSAGE_TYPES,
  SERVER_CAPABILITIES,
  MAX_AGENT_PROGRESS_NOTE_EVIDENCE_TARGETS,
  MAX_AGENT_PROGRESS_NOTE_ID_LENGTH,
  MAX_AGENT_PROGRESS_NOTE_TEXT_LENGTH,
  MAX_RETAINED_AGENT_PROGRESS_NOTE_BYTES,
  StopCmdSchema,
  TYPESCRIPT_ONLY_CLIENT_MESSAGE_TYPES,
  TYPESCRIPT_ONLY_SERVER_MESSAGE_TYPES,
  parseClientMessage,
} from "../src/server/protocol.js";
import {
  IMAGE_ATTACHMENTS_CAPABILITY,
  MAX_IMAGE_AGGREGATE_ENCODED_BYTES,
  MAX_IMAGE_ATTACHMENTS,
  MAX_IMAGE_DIMENSION,
  MAX_IMAGE_ENCODED_BYTES,
  MAX_IMAGE_RGBA_BYTES,
  MAX_IMAGE_SOURCE_BYTES,
} from "../src/types/image-attachments.js";

type RuntimeOnlyMessage = {
  reason: string;
  type: string;
};

type EventStreamEnvelopeContract = {
  fields: {
    channel: { known_values: string[] };
  };
  required_fields: string[];
  unknown_field_policy: "forbid";
  version: 1;
};

type WebSocketProtocolContract = {
  image_attachment_extension: {
    advertised_runtimes: ["typescript"];
    additive_commands: ["chat_agent", "inject_hint"];
    attachment: {
      canonical_base64: true;
      media_types: string[];
      required_fields: string[];
      sources: string[];
      unique_fields: string[];
      verified_before_provider: string[];
    };
    capability: "image_attachments_v1";
    limits: {
      max_aggregate_encoded_bytes: number;
      max_attachments: number;
      max_decoded_bytes_per_image: number;
      max_decoded_rgba_bytes_per_image: number;
      max_dimension: number;
      max_encoded_bytes_per_image: number;
    };
    python_support: "deferred";
  };
  agent_progress_note_extension: {
    advertised_runtimes: ["typescript"];
    capability: "agent_progress_notes_v1";
    event: "agent_progress_note";
    fixture: {
      client_run_id: string;
      event: "agent_progress_note";
      event_id: string;
      occurred_at: string;
      payload: Record<string, unknown>;
      run_id: string;
      sequence: number;
      type: "event";
    };
    identity: {
      payload_run_id_matches_outer_run_id: true;
      outer_transcript_fields: string[];
    };
    payload: {
      copy_limits: {
        empty_or_whitespace_only_values_allowed: false;
        text_characters: number;
      };
      evidence_targets: {
        artifact_rule: string;
        id: {
          credential_shaped_values_allowed: false;
          maximum_characters: number;
          pattern: string;
        };
        identity_rule: string;
        invalid_target_rule: string;
        maximum_items: number;
        resolution_rule: string;
        unique: true;
      };
      generation: { integer: true; minimum: 0 };
      kinds: ["intent", "discovery", "decision", "verification", "blocker"];
      optional_fields: ["evidence_targets"];
      required_fields: ["run_id", "generation", "kind", "text"];
      unknown_field_policy: "forbid";
    };
    python_support: "deferred_until_durable_transcript_metadata_is_available";
    requires_transcript_protocol_version: 1;
    retention: {
      exact_live_replay_parity: true;
      finite_horizon: string;
      full_note_or_drop: true;
      max_serialized_event_bytes: number;
      redacted_before_persistence: true;
      restart_replay: true;
    };
    safety: {
      agent_authored_summary_only: true;
      credentials_allowed: false;
      hidden_reasoning_allowed: false;
      raw_model_or_tool_io_allowed: false;
      raw_prompts_allowed: false;
      redacted_and_rebounded_before_emission: true;
      selectors_allowed: false;
      urls_allowed: false;
    };
  };
  agent_task_plan_extension: {
    advertised_runtimes: ["typescript"];
    capability: "agent_task_plan_v1";
    event: "task_plan_updated";
    requires_transcript_protocol_version: 1;
    payload: {
      completed_steps_are_sticky: true;
      copy_limits: {
        aggregate_characters: 20000;
        detail_characters: 2000;
        empty_or_whitespace_only_values_allowed: false;
        label_characters: 160;
        summary_characters: 240;
      };
      full_snapshot: true;
      id: {
        credential_shaped_values_allowed: false;
        maximum_characters: 200;
        pattern: string;
      };
      initial_plan_revision: 1;
      initial_version: 1;
      progress_revision_rule: "unchanged";
      replan_revision_rule: "strictly_increases";
      step_count: { maximum: 50; minimum: 1 };
      terminal_snapshot_rule: string;
      update_kinds: ["initial", "progress", "replan"];
    };
    python_support: "deferred_until_durable_transcript_metadata_is_available";
    retention: {
      full_snapshot_or_drop: true;
      max_serialized_event_bytes: 12288;
      redacted_before_persistence: true;
      restart_replay: true;
    };
  };
  event_stream_envelope: EventStreamEnvelopeContract;
  protocol_version: number;
  safe_stop_extension: {
    advertised_runtimes: string[];
    capability: "safe_run_stop_v1";
    command: "stop";
    python_support: "supported";
    base_idempotency: "live";
    required_command_fields: ["client_run_id", "command_id"];
    terminal_arbitration: "first_terminal_outcome_wins";
    terminal_event: "run_stopped";
    durable_reconnect_replay: {
      requires_transcript_protocol_version: 1;
      advertised_runtimes: ["typescript"];
    };
  };
  system_map_projection_extension: {
    advertised_runtimes: ["typescript"];
    capability: "system_map_projection_v1";
    channel: "cockpit";
    endpoint: "/ws/events";
    event: "system_map_transfer";
    payload: {
      optional_fields: string[];
      required_fields: string[];
      statuses: string[];
      version: 1;
    };
    python_support: "deferred";
    query: {
      optional_run_scope: "run_id";
      optional_view: {
        default: "execution";
        parameter: "view";
        values: ["execution", "context", "activation", "routing"];
      };
      projection: "system-map";
    };
    safety: {
      allowlisted_summary_fields_only: true;
      bounded_replay: true;
      credentials_allowed: false;
      raw_model_or_tool_io_allowed: false;
      raw_prompts_allowed: false;
    };
    trace: {
      paired_boundaries: string[];
      phases: string[];
      raw_payload_copied: false;
      source: "EventStreamRecord.trace";
      version: 1;
    };
  };
  shared_client_messages: string[];
  shared_server_messages: string[];
  top_level_unknown_field_policy: "forbid";
  typescript_only_client_messages: RuntimeOnlyMessage[];
  typescript_only_server_messages: RuntimeOnlyMessage[];
};

const CONTRACT = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "docs", "websocket-protocol-contract.json"),
    "utf-8",
  ),
) as WebSocketProtocolContract;

function runtimeOnlyTypes(items: RuntimeOnlyMessage[]): string[] {
  return items.map((item) => item.type);
}

describe("WebSocket protocol shared contract", () => {
  it("keeps TypeScript message inventories aligned with the shared manifest", () => {
    const tsOnlyServer = runtimeOnlyTypes(CONTRACT.typescript_only_server_messages);
    const tsOnlyClient = runtimeOnlyTypes(CONTRACT.typescript_only_client_messages);

    expect(PYTHON_SHARED_SERVER_MESSAGE_TYPES).toEqual(CONTRACT.shared_server_messages);
    expect(PYTHON_SHARED_CLIENT_MESSAGE_TYPES).toEqual(CONTRACT.shared_client_messages);
    expect(TYPESCRIPT_ONLY_SERVER_MESSAGE_TYPES).toEqual(tsOnlyServer);
    expect(TYPESCRIPT_ONLY_CLIENT_MESSAGE_TYPES).toEqual(tsOnlyClient);
    expect(SERVER_MESSAGE_TYPES).toEqual([...CONTRACT.shared_server_messages, ...tsOnlyServer]);
    expect(CLIENT_MESSAGE_TYPES).toEqual([...CONTRACT.shared_client_messages, ...tsOnlyClient]);
  });

  it("forbids unknown top-level client fields like the Python protocol", () => {
    expect(CONTRACT.top_level_unknown_field_policy).toBe("forbid");

    expect(() => parseClientMessage({ type: "pause", unexpected: true })).toThrow();
  });

  it("keeps safe stop strict and shared, with durable reconnect replay gated on transcript", () => {
    expect(CONTRACT.safe_stop_extension).toMatchObject({
      advertised_runtimes: ["typescript", "python"],
      capability: "safe_run_stop_v1",
      command: "stop",
      python_support: "supported",
      base_idempotency: "live",
      required_command_fields: ["client_run_id", "command_id"],
      terminal_arbitration: "first_terminal_outcome_wins",
      terminal_event: "run_stopped",
    });
    // The base cooperative stop is advertised by both runtimes; only durable
    // reconnect-after-terminal replay remains transcript-gated and TypeScript-only.
    expect(CONTRACT.safe_stop_extension.durable_reconnect_replay).toMatchObject({
      requires_transcript_protocol_version: 1,
      advertised_runtimes: ["typescript"],
    });
    expect(SERVER_CAPABILITIES).toContain("safe_run_stop_v1");
    expect(
      StopCmdSchema.parse({
        type: "stop",
        client_run_id: "client-run-1",
        command_id: "command-stop-1",
      }),
    ).toEqual({
      type: "stop",
      client_run_id: "client-run-1",
      command_id: "command-stop-1",
    });
    expect(() =>
      StopCmdSchema.parse({
        type: "stop",
        command_id: "command-stop-1",
      }),
    ).toThrow();
    expect(() =>
      StopCmdSchema.parse({
        type: "stop",
        client_run_id: "client-run-1",
      }),
    ).toThrow();
    expect(() =>
      StopCmdSchema.parse({
        type: "stop",
        client_run_id: "client-run-1",
        command_id: "command-stop-1",
        unexpected: true,
      }),
    ).toThrow();
  });

  it("documents the TypeScript-only redacted system-map projection", () => {
    expect(CONTRACT.system_map_projection_extension).toMatchObject({
      advertised_runtimes: ["typescript"],
      capability: "system_map_projection_v1",
      channel: "cockpit",
      endpoint: "/ws/events",
      event: "system_map_transfer",
      query: {
        projection: "system-map",
        optional_run_scope: "run_id",
        optional_view: {
          parameter: "view",
          values: ["execution", "context", "activation", "routing"],
          default: "execution",
        },
      },
      safety: {
        allowlisted_summary_fields_only: true,
        bounded_replay: true,
        credentials_allowed: false,
        raw_model_or_tool_io_allowed: false,
        raw_prompts_allowed: false,
      },
      trace: {
        version: 1,
        source: "EventStreamRecord.trace",
        phases: ["start", "complete", "instant"],
        raw_payload_copied: false,
      },
      python_support: "deferred",
    });
    expect(CONTRACT.system_map_projection_extension.payload.required_fields).toEqual(
      expect.arrayContaining(["traceId", "spanId", "spanName", "spanPhase", "startedAt"]),
    );
  });

  it("documents the exact additive image attachment contract and TypeScript parity boundary", () => {
    expect(CONTRACT.image_attachment_extension).toMatchObject({
      advertised_runtimes: ["typescript"],
      additive_commands: ["chat_agent", "inject_hint"],
      capability: IMAGE_ATTACHMENTS_CAPABILITY,
      attachment: {
        canonical_base64: true,
        sources: ["picker", "paste", "drop"],
        media_types: ["image/png", "image/jpeg", "image/gif", "image/webp"],
        unique_fields: ["id", "content_sha256"],
      },
      limits: {
        max_attachments: MAX_IMAGE_ATTACHMENTS,
        max_decoded_bytes_per_image: MAX_IMAGE_SOURCE_BYTES,
        max_encoded_bytes_per_image: MAX_IMAGE_ENCODED_BYTES,
        max_dimension: MAX_IMAGE_DIMENSION,
        max_decoded_rgba_bytes_per_image: MAX_IMAGE_RGBA_BYTES,
        max_aggregate_encoded_bytes: MAX_IMAGE_AGGREGATE_ENCODED_BYTES,
      },
      python_support: "deferred",
    });
    expect(SERVER_CAPABILITIES).not.toContain(IMAGE_ATTACHMENTS_CAPABILITY);
  });

  it("keeps agent task plans strict, replayable, and TypeScript-only", () => {
    expect(CONTRACT.agent_task_plan_extension).toMatchObject({
      advertised_runtimes: ["typescript"],
      capability: "agent_task_plan_v1",
      event: "task_plan_updated",
      requires_transcript_protocol_version: 1,
      payload: {
        completed_steps_are_sticky: true,
        copy_limits: {
          aggregate_characters: 20_000,
          detail_characters: 2_000,
          empty_or_whitespace_only_values_allowed: false,
          label_characters: 160,
          summary_characters: 240,
        },
        full_snapshot: true,
        id: {
          credential_shaped_values_allowed: false,
          maximum_characters: 200,
          pattern: "^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        },
        initial_plan_revision: 1,
        initial_version: 1,
        progress_revision_rule: "unchanged",
        replan_revision_rule: "strictly_increases",
        step_count: { maximum: 50, minimum: 1 },
        update_kinds: ["initial", "progress", "replan"],
      },
      python_support: "deferred_until_durable_transcript_metadata_is_available",
      retention: {
        full_snapshot_or_drop: true,
        max_serialized_event_bytes: 12_288,
        redacted_before_persistence: true,
        restart_replay: true,
      },
    });
    expect(SERVER_CAPABILITIES).toContain("agent_task_plan_v1");
  });

  it("keeps agent progress notes strict, consumer-compatible, and TypeScript-only", () => {
    const extension = CONTRACT.agent_progress_note_extension;
    expect(extension).toMatchObject({
      advertised_runtimes: ["typescript"],
      capability: "agent_progress_notes_v1",
      event: "agent_progress_note",
      requires_transcript_protocol_version: 1,
      payload: {
        required_fields: ["run_id", "generation", "kind", "text"],
        optional_fields: ["evidence_targets"],
        unknown_field_policy: "forbid",
        generation: { integer: true, minimum: 0 },
        kinds: ["intent", "discovery", "decision", "verification", "blocker"],
        copy_limits: {
          text_characters: MAX_AGENT_PROGRESS_NOTE_TEXT_LENGTH,
          empty_or_whitespace_only_values_allowed: false,
        },
        evidence_targets: {
          maximum_items: MAX_AGENT_PROGRESS_NOTE_EVIDENCE_TARGETS,
          unique: true,
          identity_rule: "json_tuple_of_kind_action_id_and_optional_artifact_id",
          id: {
            maximum_characters: MAX_AGENT_PROGRESS_NOTE_ID_LENGTH,
            pattern: "^[A-Za-z0-9][A-Za-z0-9._:-]*$",
            credential_shaped_values_allowed: false,
          },
          invalid_target_rule: "reject_the_entire_note",
        },
      },
      identity: {
        payload_run_id_matches_outer_run_id: true,
      },
      safety: {
        agent_authored_summary_only: true,
        hidden_reasoning_allowed: false,
        raw_prompts_allowed: false,
        raw_model_or_tool_io_allowed: false,
        credentials_allowed: false,
        urls_allowed: false,
        selectors_allowed: false,
        redacted_and_rebounded_before_emission: true,
      },
      retention: {
        full_note_or_drop: true,
        max_serialized_event_bytes: MAX_RETAINED_AGENT_PROGRESS_NOTE_BYTES,
        redacted_before_persistence: true,
        exact_live_replay_parity: true,
        restart_replay: true,
        finite_horizon: "same_as_the_retained_run_transcript",
      },
      python_support: "deferred_until_durable_transcript_metadata_is_available",
    });
    expect(extension.fixture.run_id).toBe(extension.fixture.payload.run_id);
    expect(AgentProgressNotePayloadSchema.parse(extension.fixture.payload)).toEqual(
      extension.fixture.payload,
    );
    expect(AGENT_PROGRESS_NOTE_CAPABILITY).toBe(extension.capability);
    expect(SERVER_CAPABILITIES).toContain(AGENT_PROGRESS_NOTE_CAPABILITY);
  });

  it("keeps representative shared payload shapes aligned with Python's generated schema", () => {
    expect(
      AckMsgSchema.parse({ type: "ack", action: "override_gate", decision: null }).decision,
    ).toBeNull();
    expect(() =>
      ChatAgentCmdSchema.parse({
        type: "chat_agent",
        role: "analyst",
        message: "",
      }),
    ).toThrow();
    expect(() =>
      ExecutorResourcesSchema.parse({
        docker_image: "python:3.11",
        cpu_cores: 1.5,
        memory_gb: 2,
        disk_gb: 5,
        timeout_minutes: 30,
      }),
    ).toThrow();
    expect(() =>
      ScenarioErrorMsgSchema.parse({
        type: "scenario_error",
        message: "missing stage",
      }),
    ).toThrow();
    expect(() =>
      MonitorAlertMsgSchema.parse({
        type: "monitor_alert",
        alert_id: "a1",
        condition_id: "c1",
        condition_name: "threshold",
        condition_type: "metric_threshold",
        scope: "run:r1",
        detail: { reason: "too high" },
      }),
    ).toThrow();
  });

  it("requires runtime-only messages to carry an explicit reason", () => {
    const allRuntimeOnly = [
      ...CONTRACT.typescript_only_client_messages,
      ...CONTRACT.typescript_only_server_messages,
    ];

    expect(allRuntimeOnly.length).toBeGreaterThan(0);
    for (const item of allRuntimeOnly) {
      expect(item.reason.trim().length).toBeGreaterThan(0);
    }
  });

  it("keeps the event-stream envelope aligned with the shared manifest", () => {
    const envelope = buildEventStreamEnvelope({
      channel: "generation",
      event: "run_started",
      payload: { run_id: "run_1" },
      seq: 1,
      timestamp: "2026-04-09T14:00:00.000Z",
    });

    expect(Object.keys(envelope).sort()).toEqual(
      [...CONTRACT.event_stream_envelope.required_fields].sort(),
    );
    expect(envelope.v).toBe(CONTRACT.event_stream_envelope.version);
    expect(envelope.seq).toBe(1);
    expect(CONTRACT.event_stream_envelope.unknown_field_policy).toBe("forbid");
    expect(CONTRACT.event_stream_envelope.fields.channel.known_values).toEqual(
      expect.arrayContaining(["generation", "mission", "notebook", "cockpit"]),
    );
  });
});
