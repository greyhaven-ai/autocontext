import {
  mkdtempSync,
  readdirSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  FileRuntimeActivationJournalStore,
  createRegistryRuntimeActivationPointerStore,
  createRuntimeSessionActivationAuditEventSink,
  type RuntimeActivationJournalRecord,
} from "../src/control-plane/activation/index.js";
import { normalizeBackgroundSessionTimeline } from "../src/session/background-session-events.js";
import {
  RuntimeSessionEventLog,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";
import { buildRuntimeSessionTimeline } from "../src/session/runtime-session-timeline.js";
import type {
  EnvironmentTag,
  Scenario,
} from "../src/control-plane/contract/branded-ids.js";

const roots: string[] = [];

afterEach(() => {
  for (const root of roots.splice(0)) {
    rmSync(root, { recursive: true, force: true });
  }
});

describe("runtime activation persistence", () => {
  it("atomically round-trips a durable journal across store instances", () => {
    const root = temporaryRoot();
    const record = journalRecord();
    new FileRuntimeActivationJournalStore(root).save(record);

    const reopened = new FileRuntimeActivationJournalStore(root);
    expect(reopened.load(record.transactionId)).toEqual(record);
    expect(reopened.list()).toEqual([record]);
    expect(readdirSync(join(
      root,
      ".autocontext",
      "state",
      "runtime-activation-journal",
    )).filter((name) => name.endsWith(".tmp"))).toEqual([]);
  });

  it("adapts the existing atomic registry pointer without weakening id validation", () => {
    const root = temporaryRoot();
    const pointer = createRegistryRuntimeActivationPointerStore({
      registryRoot: root,
      scenario: "grid_ctf" as Scenario,
      actuatorType: "prompt-patch",
      environmentTag: "production" as EnvironmentTag,
    });
    const artifactId = "01KPEYB3BRQWK2WSHK9E93N6NP";

    pointer.write({ artifactId, asOf: "2026-08-14T00:00:00.000Z" });
    expect(pointer.read()).toEqual({
      artifactId,
      asOf: "2026-08-14T00:00:00.000Z",
    });
    expect(() => pointer.write({
      artifactId: "../../invalid",
      asOf: "2026-08-14T00:00:01.000Z",
    })).toThrow("artifact id is invalid");
    pointer.clear();
    expect(pointer.read()).toBeNull();
  });

  it("persists sanitized transaction stages into operator timelines", () => {
    const log = RuntimeSessionEventLog.create({ sessionId: "activation-session" });
    const sink = createRuntimeSessionActivationAuditEventSink(log);

    sink.onRuntimeActivationAuditEvent({
      transactionId: "activate-1",
      operation: "activate",
      candidateArtifactId: "candidate-safe-id",
      priorArtifactId: "baseline-safe-id",
      stage: "failed",
      outcome: "failed",
      failureCode: "activation_failed",
    });

    expect(log.events[0]).toMatchObject({
      eventType: RuntimeSessionEventType.RUNTIME_ACTIVATION,
      payload: {
        transactionId: "activate-1",
        candidateArtifactId: "candidate-safe-id",
        stage: "failed",
        outcome: "failed",
        failureCode: "activation_failed",
      },
    });
    expect(normalizeBackgroundSessionTimeline(log)[0]).toMatchObject({
      title: "Runtime activation transaction",
      status: "failed",
      payload_summary: {
        transaction_id: "activate-1",
        failure_code: "activation_failed",
      },
    });
    expect(buildRuntimeSessionTimeline(log).items[0]).toMatchObject({
      event_type: "runtime_activation",
      details: {
        transactionId: "activate-1",
        failureCode: "activation_failed",
      },
    });
    expect(RuntimeSessionEventLog.fromJSON(log.toJSON()).events[0]?.eventType)
      .toBe(RuntimeSessionEventType.RUNTIME_ACTIVATION);
  });
});

function temporaryRoot(): string {
  const root = mkdtempSync(join(tmpdir(), "autoctx-runtime-activation-"));
  roots.push(root);
  return root;
}

function journalRecord(): RuntimeActivationJournalRecord {
  return {
    schemaVersion: 1,
    transactionId: "activate-candidate-1",
    operation: "activate",
    candidateArtifactId: "candidate",
    priorArtifactId: "baseline",
    targetMode: "active",
    stage: "activated",
    outcome: "in_progress",
    entries: [{
      sequence: 0,
      stage: "activated",
      outcome: "succeeded",
      timestamp: "2026-08-14T00:00:00.000Z",
    }],
    createdAt: "2026-08-14T00:00:00.000Z",
    updatedAt: "2026-08-14T00:00:00.000Z",
  };
}
