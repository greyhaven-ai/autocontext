import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import type { EventStreamRecord } from "../src/loop/events.js";
import { EventStreamEmitter } from "../src/loop/events.js";
import { GenerationRunner } from "../src/loop/generation-runner.js";
import { DeterministicProvider } from "../src/providers/deterministic.js";
import { GridCtfScenario } from "../src/scenarios/grid-ctf.js";
import { asDbPath, asRunId } from "../src/domain/ids.js";
import { SQLiteStore } from "../src/storage/index.js";
import { renderSystemMapHtml } from "../src/server/system-map-page.js";
import {
  CONTEXT_LINEAGE_TOPOLOGY,
  PROVIDER_ROUTING_TOPOLOGY,
  projectSystemMapTransfer,
  readSystemMapView,
  readSystemMapReplay,
  RUNTIME_ACTIVATION_TOPOLOGY,
  SYSTEM_MAP_TOPOLOGY,
} from "../src/server/system-map.js";

const tempDirs: string[] = [];
const __dirname = dirname(fileURLToPath(import.meta.url));

afterEach(() => {
  for (const dir of tempDirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe("system-map topology", () => {
  it("keeps stable unique nodes and valid directed edges", () => {
    const nodeIds = SYSTEM_MAP_TOPOLOGY.nodes.map((node) => node.id);
    const edgeIds = SYSTEM_MAP_TOPOLOGY.edges.map((edge) => edge.id);
    const districtIds = SYSTEM_MAP_TOPOLOGY.districts.map((district) => district.id);
    expect(new Set(nodeIds).size).toBe(nodeIds.length);
    expect(new Set(edgeIds).size).toBe(edgeIds.length);
    expect(new Set(districtIds).size).toBe(districtIds.length);
    expect(new Set(SYSTEM_MAP_TOPOLOGY.districts.map((district) => district.color)).size)
      .toBe(SYSTEM_MAP_TOPOLOGY.districts.length);
    for (const edge of SYSTEM_MAP_TOPOLOGY.edges) {
      expect(nodeIds).toContain(edge.from);
      expect(nodeIds).toContain(edge.to);
    }
    for (let i = 0; i < SYSTEM_MAP_TOPOLOGY.nodes.length; i += 1) {
      for (let j = i + 1; j < SYSTEM_MAP_TOPOLOGY.nodes.length; j += 1) {
        const a = SYSTEM_MAP_TOPOLOGY.nodes[i]!;
        const b = SYSTEM_MAP_TOPOLOGY.nodes[j]!;
        const horizontalGap = Math.max(a.x - (b.x + b.width), b.x - (a.x + a.width));
        const verticalGap = Math.max(a.y - (b.y + b.depth), b.y - (a.y + a.depth));
        expect(
          horizontalGap >= 0.65 || verticalGap >= 0.65,
          `${a.id} and ${b.id} need more layout separation`,
        ).toBe(true);
      }
    }
    expect(new Set(SYSTEM_MAP_TOPOLOGY.nodes.map((node) => node.kind))).toEqual(
      new Set(["rack", "slab", "tower", "vault"]),
    );
    const districtByGroup = new Map(
      SYSTEM_MAP_TOPOLOGY.districts.map((district) => [district.group, district]),
    );
    expect(new Set(districtByGroup.keys())).toEqual(
      new Set(SYSTEM_MAP_TOPOLOGY.nodes.map((node) => node.group)),
    );
    for (const node of SYSTEM_MAP_TOPOLOGY.nodes) {
      const district = districtByGroup.get(node.group)!;
      expect(node.x).toBeGreaterThanOrEqual(district.x);
      expect(node.y).toBeGreaterThanOrEqual(district.y);
      expect(node.x + node.width).toBeLessThanOrEqual(district.x + district.width);
      expect(node.y + node.depth).toBeLessThanOrEqual(district.y + district.depth);
    }
    for (let i = 0; i < SYSTEM_MAP_TOPOLOGY.districts.length; i += 1) {
      for (let j = i + 1; j < SYSTEM_MAP_TOPOLOGY.districts.length; j += 1) {
        const a = SYSTEM_MAP_TOPOLOGY.districts[i]!;
        const b = SYSTEM_MAP_TOPOLOGY.districts[j]!;
        const horizontalGap = Math.max(a.x - (b.x + b.width), b.x - (a.x + a.width));
        const verticalGap = Math.max(a.y - (b.y + b.depth), b.y - (a.y + a.depth));
        expect(
          horizontalGap >= 0.2 || verticalGap >= 0.2,
          `${a.id} and ${b.id} districts must stay visually separate`,
        ).toBe(true);
      }
    }
  });

  it("renders a self-contained isometric live explorer", () => {
    const html = renderSystemMapHtml();
    expect(html).toContain("<title>autocontext — live system map</title>");
    expect(html).toContain("url.searchParams.set('projection', projection)");
    expect(html).toContain("Trace one step");
    expect(html).toContain(">Trace details</button>");
    expect(html).toContain(">Live log</button>");
    expect(html).toContain("async function drainPlaybackQueue()");
    expect(html).toContain("recordTransfer(transfer, 'live')");
    expect(html).toContain("pendingTransfers.push(transfer)");
    expect(html).toContain("await animatePacket(edge, transfer)");
    expect(html).toContain("Showing every observed transfer entering or leaving");
    expect(html).toContain("row.dataset.from === selectedNodeId");
    expect(html).toContain("clearLogFilterButton.addEventListener('click', clearLogFilter)");
    expect(html).toContain("aria-pressed");
    expect(html).toContain("function drawDistricts()");
    expect(html).toContain("const gridPadding = .5");
    expect(html).toContain("district.x + district.width");
    expect(html).toContain('class="timeline-shell"');
    expect(html).toContain('id="timeline-scrubber"');
    expect(html).toContain("function scrubTimelineTo(index)");
    expect(html).toContain("function causalTimelineTransfers(transfer)");
    expect(html).toContain("function followLatestTimeline()");
    expect(html).toContain("timeline-focused");
    expect(html).toContain("class: 'building-beacon'");
    expect(html).toContain("district-breathe");
    expect(html).toContain("district-color-' + district.color");
    expect(html).toContain("--category-purple");
    expect(html).toContain('id="component-event-layer"');
    expect(html).toContain('id="building-label-layer"');
    expect(html).toContain("function drawComponentActivity()");
    expect(html).toContain("function recordComponentActivity(transfer, origin)");
    expect(html).toContain("activity-stack-trigger");
    expect(html).toContain("activity-leader");
    expect(html).toContain("function setLiveComponentCallouts(transfer, active)");
    expect(html).toContain("const panelTop = Math.max(96");
    expect(html).toContain("labelBounds.left - stageBounds.left - 34");
    expect(html).toContain("setExpandedActivity(node.id, true)");
    expect(html).toContain("componentActivityRecords.forEach");
    expect(html).toContain("getPointAtLength");
    expect(html).toContain("const isometricCamera");
    expect(html).toContain("const xScale = 27");
    expect(html).toContain("class: usesBaseLabels ? 'building-label-base'");
    expect(html).toContain("node.x + node.width / 2 + .52");
    expect(html).toContain("node.y + node.depth + .52");
    expect(html).toContain("buildingLabelElements.get(node.id)");
    expect(html).toContain("district.id === 'live-state'");
    expect(html).toContain("district.id === 'model-execution'");
    expect(html).not.toContain("__AUTOCONTEXT_SYSTEM_MAP_");
    expect(html).not.toContain("<script src=");
    expect(html).not.toContain("https://");
    expect(html).not.toContain("MTS");
  });

  it("keeps context lineage as a valid, separately spaced topology", () => {
    expect(CONTEXT_LINEAGE_TOPOLOGY.view).toBe("context");
    expect(CONTEXT_LINEAGE_TOPOLOGY.title).toBe("Context + memory lineage");
    expect(readSystemMapView("context")).toBe("context");
    expect(readSystemMapView("unknown")).toBe("execution");

    const nodeIds = CONTEXT_LINEAGE_TOPOLOGY.nodes.map((node) => node.id);
    const districtByGroup = new Map(
      CONTEXT_LINEAGE_TOPOLOGY.districts.map((district) => [district.group, district]),
    );
    expect(new Set(nodeIds).size).toBe(nodeIds.length);
    expect(new Set(CONTEXT_LINEAGE_TOPOLOGY.districts.map((district) => district.color)).size)
      .toBe(CONTEXT_LINEAGE_TOPOLOGY.districts.length);
    for (const edge of CONTEXT_LINEAGE_TOPOLOGY.edges) {
      expect(nodeIds).toContain(edge.from);
      expect(nodeIds).toContain(edge.to);
    }
    for (const node of CONTEXT_LINEAGE_TOPOLOGY.nodes) {
      const district = districtByGroup.get(node.group)!;
      expect(district, `${node.id} must belong to a district`).toBeDefined();
      expect(node.x).toBeGreaterThanOrEqual(district.x);
      expect(node.y).toBeGreaterThanOrEqual(district.y);
      expect(node.x + node.width).toBeLessThanOrEqual(district.x + district.width);
      expect(node.y + node.depth).toBeLessThanOrEqual(district.y + district.depth);
    }
    for (let i = 0; i < CONTEXT_LINEAGE_TOPOLOGY.nodes.length; i += 1) {
      for (let j = i + 1; j < CONTEXT_LINEAGE_TOPOLOGY.nodes.length; j += 1) {
        const a = CONTEXT_LINEAGE_TOPOLOGY.nodes[i]!;
        const b = CONTEXT_LINEAGE_TOPOLOGY.nodes[j]!;
        const horizontalGap = Math.max(a.x - (b.x + b.width), b.x - (a.x + a.width));
        const verticalGap = Math.max(a.y - (b.y + b.depth), b.y - (a.y + a.depth));
        expect(
          horizontalGap >= 0.5 || verticalGap >= 0.5,
          `${a.id} and ${b.id} need more context-lineage separation`,
        ).toBe(true);
      }
    }

    const html = renderSystemMapHtml(CONTEXT_LINEAGE_TOPOLOGY);
    expect(html).toContain('const mapView = topology.view');
    expect(html).toContain('id="context-lens"');
    expect(html).toContain('"view":"context"');
    expect(html).toContain("replayUrl.searchParams.set('view', mapView)");
    expect(html).not.toContain("__AUTOCONTEXT_SYSTEM_MAP_");
  });

  it("keeps runtime activation and rollback as a valid, separately spaced topology", () => {
    expect(RUNTIME_ACTIVATION_TOPOLOGY.view).toBe("activation");
    expect(RUNTIME_ACTIVATION_TOPOLOGY.title).toBe("Runtime activation + rollback");
    expect(readSystemMapView("activation")).toBe("activation");

    const nodeIds = RUNTIME_ACTIVATION_TOPOLOGY.nodes.map((node) => node.id);
    const districtByGroup = new Map(
      RUNTIME_ACTIVATION_TOPOLOGY.districts.map((district) => [district.group, district]),
    );
    expect(new Set(nodeIds).size).toBe(nodeIds.length);
    expect(new Set(RUNTIME_ACTIVATION_TOPOLOGY.edges.map((edge) => edge.id)).size)
      .toBe(RUNTIME_ACTIVATION_TOPOLOGY.edges.length);
    expect(new Set(RUNTIME_ACTIVATION_TOPOLOGY.districts.map((district) => district.color)).size)
      .toBe(RUNTIME_ACTIVATION_TOPOLOGY.districts.length);
    expect(new Set(RUNTIME_ACTIVATION_TOPOLOGY.nodes.map((node) => node.kind))).toEqual(
      new Set(["rack", "slab", "tower", "vault"]),
    );
    for (const edge of RUNTIME_ACTIVATION_TOPOLOGY.edges) {
      expect(nodeIds).toContain(edge.from);
      expect(nodeIds).toContain(edge.to);
    }
    for (const node of RUNTIME_ACTIVATION_TOPOLOGY.nodes) {
      const district = districtByGroup.get(node.group)!;
      expect(district, `${node.id} must belong to a district`).toBeDefined();
      expect(node.x).toBeGreaterThanOrEqual(district.x);
      expect(node.y).toBeGreaterThanOrEqual(district.y);
      expect(node.x + node.width).toBeLessThanOrEqual(district.x + district.width);
      expect(node.y + node.depth).toBeLessThanOrEqual(district.y + district.depth);
    }
    for (let i = 0; i < RUNTIME_ACTIVATION_TOPOLOGY.nodes.length; i += 1) {
      for (let j = i + 1; j < RUNTIME_ACTIVATION_TOPOLOGY.nodes.length; j += 1) {
        const a = RUNTIME_ACTIVATION_TOPOLOGY.nodes[i]!;
        const b = RUNTIME_ACTIVATION_TOPOLOGY.nodes[j]!;
        const horizontalGap = Math.max(a.x - (b.x + b.width), b.x - (a.x + a.width));
        const verticalGap = Math.max(a.y - (b.y + b.depth), b.y - (a.y + a.depth));
        expect(
          horizontalGap >= 0.5 || verticalGap >= 0.5,
          `${a.id} and ${b.id} need more activation-map separation`,
        ).toBe(true);
      }
    }

    const html = renderSystemMapHtml(RUNTIME_ACTIVATION_TOPOLOGY);
    expect(html).toContain('id="activation-lens"');
    expect(html).toContain('"view":"activation"');
    expect(html).toContain("live activation topology");
    expect(html).not.toContain("__AUTOCONTEXT_SYSTEM_MAP_");
  });

  it("keeps provider and model routing as a valid, separately spaced topology", () => {
    expect(PROVIDER_ROUTING_TOPOLOGY.view).toBe("routing");
    expect(PROVIDER_ROUTING_TOPOLOGY.title).toBe("Provider + model routing");
    expect(readSystemMapView("routing")).toBe("routing");

    const nodeIds = PROVIDER_ROUTING_TOPOLOGY.nodes.map((node) => node.id);
    const districtByGroup = new Map(
      PROVIDER_ROUTING_TOPOLOGY.districts.map((district) => [district.group, district]),
    );
    expect(new Set(nodeIds).size).toBe(nodeIds.length);
    expect(new Set(PROVIDER_ROUTING_TOPOLOGY.edges.map((edge) => edge.id)).size)
      .toBe(PROVIDER_ROUTING_TOPOLOGY.edges.length);
    expect(new Set(PROVIDER_ROUTING_TOPOLOGY.districts.map((district) => district.color)).size)
      .toBe(PROVIDER_ROUTING_TOPOLOGY.districts.length);
    expect(new Set(PROVIDER_ROUTING_TOPOLOGY.nodes.map((node) => node.kind))).toEqual(
      new Set(["rack", "slab", "tower", "vault"]),
    );
    for (const edge of PROVIDER_ROUTING_TOPOLOGY.edges) {
      expect(nodeIds).toContain(edge.from);
      expect(nodeIds).toContain(edge.to);
    }
    for (const node of PROVIDER_ROUTING_TOPOLOGY.nodes) {
      const district = districtByGroup.get(node.group)!;
      expect(district, `${node.id} must belong to a district`).toBeDefined();
      expect(node.x).toBeGreaterThanOrEqual(district.x);
      expect(node.y).toBeGreaterThanOrEqual(district.y);
      expect(node.x + node.width).toBeLessThanOrEqual(district.x + district.width);
      expect(node.y + node.depth).toBeLessThanOrEqual(district.y + district.depth);
    }
    for (let i = 0; i < PROVIDER_ROUTING_TOPOLOGY.nodes.length; i += 1) {
      for (let j = i + 1; j < PROVIDER_ROUTING_TOPOLOGY.nodes.length; j += 1) {
        const a = PROVIDER_ROUTING_TOPOLOGY.nodes[i]!;
        const b = PROVIDER_ROUTING_TOPOLOGY.nodes[j]!;
        const horizontalGap = Math.max(a.x - (b.x + b.width), b.x - (a.x + a.width));
        const verticalGap = Math.max(a.y - (b.y + b.depth), b.y - (a.y + a.depth));
        expect(
          horizontalGap >= 0.5 || verticalGap >= 0.5,
          `${a.id} and ${b.id} need more provider-routing separation`,
        ).toBe(true);
      }
    }

    const html = renderSystemMapHtml(PROVIDER_ROUTING_TOPOLOGY);
    expect(html).toContain('id="routing-lens"');
    expect(html).toContain('"view":"routing"');
    expect(html).toContain("live provider routing");
    expect(html).toContain("const usesBaseLabels");
    expect(html).not.toContain("__AUTOCONTEXT_SYSTEM_MAP_");
  });
});

describe("system-map event projection", () => {
  it("routes recursive harness events over their real topology edges", () => {
    expect(projectSystemMapTransfer(record("generation_started", {
      run_id: "run-1",
      generation: 2,
    }))).toMatchObject({
      edgeId: "runner-knowledge",
      from: "runner",
      to: "knowledge",
      generation: 2,
      status: "started",
    });

    expect(projectSystemMapTransfer(record("role_completed", {
      run_id: "run-1",
      generation: 2,
      role: "architect",
      latency_ms: 742,
    }, 2))).toMatchObject({
      edgeId: "translator-architect",
      durationMs: 742,
      kind: "role",
    });

    expect(projectSystemMapTransfer(record("gate_decided", {
      run_id: "run-1",
      generation: 2,
      gate_decision: "retry",
      reason: "score delta below threshold",
    }, 3))).toMatchObject({
      edgeId: "gate-validation",
      status: "retry",
      kind: "retry",
    });
  });

  it("projects runtime component graph activity without copying arbitrary payloads", () => {
    const transfer = projectSystemMapTransfer(record("runtime_session_event", {
      session_id: "run:runtime-1",
      event: {
        event_type: "component_graph",
        payload: {
          revision: 4,
          operation: "activate",
          outcome: "completed",
          componentId: "provider-router",
          capabilityId: "provider.route",
          prompt: "do not expose this",
          api_key: "secret",
        },
      },
    }));

    expect(transfer).toMatchObject({
      edgeId: "runtime-runner",
      runId: "run:runtime-1",
      event: "component_graph",
      kind: "lifecycle",
      summary: {
        revision: 4,
        operation: "activate",
        outcome: "completed",
        componentId: "provider-router",
        capabilityId: "provider.route",
      },
    });
    expect(transfer?.summary).not.toHaveProperty("prompt");
    expect(transfer?.summary).not.toHaveProperty("api_key");
  });

  it("projects context and memory lineage without copying context content", () => {
    expect(projectSystemMapTransfer(record("generation_started", {
      run_id: "run-context",
      generation: 2,
    }), "context")).toMatchObject({
      edgeId: "playbook-selection",
      from: "playbook",
      to: "selection",
      kind: "context",
    });

    expect(projectSystemMapTransfer(record("role_completed", {
      run_id: "run-context",
      generation: 2,
      role: "competitor",
      prompt: "must not be projected",
      output: "must not be projected",
    }, 2), "context")).toMatchObject({
      edgeId: "competitor-candidate",
      from: "competitor",
      to: "candidate",
      summary: { role: "competitor", generation: 2 },
    });

    const compaction = projectSystemMapTransfer(record("runtime_session_event", {
      session_id: "run-context",
      event: {
        event_type: "compaction",
        payload: {
          runId: "run-context",
          generation: 2,
          entryCount: 3,
          components: "playbook, trajectory",
          tokensBefore: 9100,
          summary: "must not be copied",
        },
      },
    }, 3), "context");
    expect(compaction).toMatchObject({
      edgeId: "selection-compaction",
      summary: {
        generation: 2,
        entryCount: 3,
        components: "playbook, trajectory",
        tokensBefore: 9100,
      },
    });
    expect(compaction?.summary).not.toHaveProperty("prompt");
    expect(compaction?.summary).not.toHaveProperty("output");
    expect(compaction?.summary).not.toHaveProperty("summary");
  });

  it("projects activation, component graph, and rollback events onto their real stages", () => {
    const staged = projectSystemMapTransfer(record("runtime_session_event", {
      session_id: "runtime-activation-1",
      event: {
        event_type: "runtime_activation",
        payload: {
          transactionId: "tx-1",
          operation: "activate",
          candidateArtifactId: "artifact-candidate",
          priorArtifactId: "artifact-prior",
          stage: "applying",
          outcome: "in_progress",
          rawArtifact: "must not be copied",
        },
      },
    }), "activation");
    expect(staged).toMatchObject({
      edgeId: "ledger-driver",
      from: "stage-ledger",
      to: "graph-driver",
      status: "started",
      kind: "activation",
      summary: {
        transactionId: "tx-1",
        operation: "activate",
        candidateArtifactId: "artifact-candidate",
        priorArtifactId: "artifact-prior",
        stage: "applying",
        outcome: "in_progress",
      },
    });
    expect(staged?.summary).not.toHaveProperty("rawArtifact");

    expect(projectSystemMapTransfer(record("runtime_session_event", {
      session_id: "runtime-activation-1",
      event: {
        event_type: "component_graph",
        payload: {
          revision: 5,
          operation: "component_waiting",
          outcome: "waiting",
          componentId: "model-router",
          capabilityId: "provider.route",
        },
      },
    }, 2), "activation")).toMatchObject({
      edgeId: "dependencies-bindings",
      status: "retry",
      summary: { revision: 5, componentId: "model-router", capabilityId: "provider.route" },
    });

    expect(projectSystemMapTransfer(record("runtime_session_event", {
      session_id: "runtime-activation-1",
      event: {
        event_type: "runtime_activation",
        payload: {
          transactionId: "tx-1",
          operation: "rollback",
          stage: "reverting",
          outcome: "in_progress",
        },
      },
    }, 3), "activation")).toMatchObject({
      edgeId: "failure-unwind",
      from: "failure-signal",
      to: "unwind-effects",
      status: "retry",
      kind: "rollback",
    });

    expect(projectSystemMapTransfer(record("role_completed", {
      run_id: "runtime-activation-1",
      role: "competitor",
    }, 4), "activation")).toBeNull();
  });

  it("projects provider selection, model responses, retries, and failures", () => {
    expect(projectSystemMapTransfer(record("generation_started", {
      run_id: "run-routing",
      generation: 2,
    }), "routing")).toMatchObject({
      edgeId: "input-role-router",
      from: "input-envelope",
      to: "role-router",
      kind: "routing",
    });

    const selected = projectSystemMapTransfer(record("role_started", {
      run_id: "run-routing",
      generation: 2,
      role: "competitor",
      attempt: 1,
      provider: "openai",
      model: "gpt-5.6-luna",
      input_bytes: 1436,
      prompt: "must not be copied",
    }, 2), "routing");
    expect(selected).toMatchObject({
      edgeId: "guardrails-adapter",
      from: "guardrails",
      to: "provider-adapter",
      kind: "routing",
      label: "competitor · openai / gpt-5.6-luna",
      summary: {
        role: "competitor",
        attempt: 1,
        provider: "openai",
        model: "gpt-5.6-luna",
        input_bytes: 1436,
      },
    });
    expect(selected?.summary).not.toHaveProperty("prompt");

    expect(projectSystemMapTransfer(record("role_completed", {
      run_id: "run-routing",
      generation: 2,
      role: "competitor",
      provider: "openai",
      model: "gpt-5.6-luna",
      latency_ms: 412,
      tokens: 812,
    }, 3), "routing")).toMatchObject({
      edgeId: "completion-response",
      status: "completed",
      durationMs: 412,
      summary: { latency_ms: 412, tokens: 812 },
    });

    expect(projectSystemMapTransfer(record("role_started", {
      run_id: "run-routing",
      generation: 2,
      role: "competitor",
      attempt: 2,
      provider: "anthropic",
      model: "claude-sonnet-5",
    }, 4), "routing")).toMatchObject({
      edgeId: "backoff-retry",
      status: "started",
      kind: "retry",
    });

    expect(projectSystemMapTransfer(record("role_failed", {
      run_id: "run-routing",
      generation: 2,
      role: "competitor",
      status: "failed",
      reason: "ProviderError",
    }, 5), "routing")).toMatchObject({
      edgeId: "transport-failure",
      status: "failed",
      kind: "failure",
    });

    expect(projectSystemMapTransfer(record("tournament_completed", {
      run_id: "run-routing",
      generation: 2,
    }, 6), "routing")).toBeNull();
  });

  it("replays only recognized projected transfers in source order", () => {
    const dir = mkdtempSync(join(tmpdir(), "autocontext-system-map-"));
    tempDirs.push(dir);
    const path = join(dir, "events.ndjson");
    writeFileSync(path, [
      JSON.stringify(record("run_started", { run_id: "run-1" }, 1)),
      "not-json",
      JSON.stringify(record("unknown_event", { run_id: "run-1" }, 2)),
      JSON.stringify(record("generation_started", { run_id: "run-1", generation: 1 }, 3)),
      JSON.stringify(record("gate_decided", { run_id: "run-1", generation: 1, gate_decision: "advance" }, 4)),
    ].join("\n"), "utf-8");

    const transfers = readSystemMapReplay(path, 2);
    expect(transfers.map((transfer) => transfer.sourceSequence)).toEqual([3, 4]);
    expect(transfers.map((transfer) => transfer.edgeId)).toEqual([
      "runner-knowledge",
      "gate-curation",
    ]);
  });

  it("backs a deterministic generation with paired, recorded harness spans", async () => {
    const dir = mkdtempSync(join(tmpdir(), "autocontext-system-map-trace-"));
    tempDirs.push(dir);
    const store = new SQLiteStore(asDbPath(join(dir, "trace.db")));
    store.migrate(join(__dirname, "..", "migrations"));
    let timestampMs = Date.parse("2026-08-16T12:00:00.000Z");
    const events = new EventStreamEmitter(join(dir, "events.ndjson"), {
      now: () => new Date(timestampMs += 25),
    });
    const records: EventStreamRecord[] = [];
    events.subscribe((_event, _payload, eventRecord) => {
      if (eventRecord) records.push(eventRecord);
    });
    const runner = new GenerationRunner({
      provider: new DeterministicProvider(),
      scenario: new GridCtfScenario(),
      store,
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      matchesPerGeneration: 1,
      maxRetries: 0,
      minDelta: 0,
      events,
    });

    try {
      await runner.run(asRunId("system-map-trace"), 1);
    } finally {
      store.close();
    }

    const transfers = records
      .map((eventRecord) => projectSystemMapTransfer(eventRecord))
      .filter((transfer) => transfer !== null);
    expect(transfers.length).toBeGreaterThan(12);
    expect(transfers.every((transfer) => (
      transfer.traceId.startsWith("autocontext:")
      && transfer.spanId.startsWith(transfer.traceId)
      && transfer.startedAt.length > 0
      && typeof transfer.payloadBytes === "number"
    ))).toBe(true);
    const replayed = readSystemMapReplay(events.path, 250);
    expect(replayed.map((transfer) => transfer.spanId)).toEqual(
      transfers.map((transfer) => transfer.spanId),
    );

    expectPairedSpan(transfers, "role_started", "role_completed", "role.competitor");
    expectPairedSpan(transfers, "tournament_started", "tournament_completed", "tournament");
    expectPairedSpan(transfers, "persistence_started", "persistence_completed", "persistence");

    const competitor = transfers.find((transfer) => (
      transfer.event === "role_completed" && transfer.summary.role === "competitor"
    ));
    expect(competitor).toMatchObject({
      spanPhase: "complete",
      summary: {
        provider: "deterministic",
        model: "deterministic-dev",
      },
    });
    expect(competitor?.summary.input_bytes).toEqual(expect.any(Number));
    expect(competitor?.summary).not.toHaveProperty("prompt");
  });
});

function expectPairedSpan(
  transfers: NonNullable<ReturnType<typeof projectSystemMapTransfer>>[],
  startedEvent: string,
  completedEvent: string,
  spanName: string,
): void {
  const started = transfers.find((transfer) => (
    transfer.event === startedEvent && transfer.spanName === spanName
  ));
  const completed = transfers.find((transfer) => (
    transfer.event === completedEvent && transfer.spanName === spanName
  ));
  expect(started).toMatchObject({ spanPhase: "start" });
  expect(completed).toMatchObject({ spanPhase: "complete" });
  expect(completed?.spanId).toBe(started?.spanId);
  expect(completed!.sourceSequence).toBeGreaterThan(started!.sourceSequence);
  expect(completed?.parentSpanId).toBe(started?.parentSpanId);
  expect(completed?.endedAt).toBeTruthy();
  expect(completed?.durationMs).toEqual(expect.any(Number));
}

function record(
  event: string,
  payload: Record<string, unknown>,
  seq = 1,
): EventStreamRecord {
  return {
    channel: "generation",
    event,
    payload,
    seq,
    ts: `2026-08-16T12:00:0${seq}.000Z`,
    v: 1,
  };
}
