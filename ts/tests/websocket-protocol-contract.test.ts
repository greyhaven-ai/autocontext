import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { buildEventStreamEnvelope } from "../src/server/event-stream-envelope.js";
import {
  AckMsgSchema,
  CLIENT_MESSAGE_TYPES,
  ChatAgentCmdSchema,
  ExecutorResourcesSchema,
  MonitorAlertMsgSchema,
  PYTHON_SHARED_CLIENT_MESSAGE_TYPES,
  PYTHON_SHARED_SERVER_MESSAGE_TYPES,
  ScenarioErrorMsgSchema,
  SERVER_MESSAGE_TYPES,
  TYPESCRIPT_ONLY_CLIENT_MESSAGE_TYPES,
  TYPESCRIPT_ONLY_SERVER_MESSAGE_TYPES,
  parseClientMessage,
} from "../src/server/protocol.js";

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
  event_stream_envelope: EventStreamEnvelopeContract;
  protocol_version: number;
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

  it("keeps representative shared payload shapes aligned with Python's generated schema", () => {
    expect(AckMsgSchema.parse({ type: "ack", action: "override_gate", decision: null }).decision)
      .toBeNull();
    expect(() => ChatAgentCmdSchema.parse({
      type: "chat_agent",
      role: "analyst",
      message: "",
    })).toThrow();
    expect(() => ExecutorResourcesSchema.parse({
      docker_image: "python:3.11",
      cpu_cores: 1.5,
      memory_gb: 2,
      disk_gb: 5,
      timeout_minutes: 30,
    })).toThrow();
    expect(() => ScenarioErrorMsgSchema.parse({
      type: "scenario_error",
      message: "missing stage",
    })).toThrow();
    expect(() => MonitorAlertMsgSchema.parse({
      type: "monitor_alert",
      alert_id: "a1",
      condition_id: "c1",
      condition_name: "threshold",
      condition_type: "metric_threshold",
      scope: "run:r1",
      detail: { reason: "too high" },
    })).toThrow();
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
