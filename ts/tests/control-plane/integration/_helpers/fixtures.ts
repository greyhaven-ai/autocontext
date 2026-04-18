// Shared integration-test fixtures for Layer 10 flows 1-3 (and a subset of
// 4-6). Each helper is a thin wrapper over the real public API of the
// individual layers — no mocking — so the fixture exercises the same code
// path the CLI does.
//
// Conventions:
//   - Tmp directories are owned by the caller (each test creates + tears down
//     its own root). These helpers DO NOT write outside of the supplied paths.
//   - Time is supplied explicitly (no Date.now()) so the resulting registry is
//     reproducible across runs.

import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import "../../../../src/control-plane/actuators/index.js"; // register actuators
import {
  parseScenario,
  parseSuiteId,
  parseEnvironmentTag,
  defaultEnvironmentTag,
  type EnvironmentTag,
  type Scenario,
  type SuiteId,
} from "../../../../src/control-plane/contract/branded-ids.js";
import {
  createArtifact,
  createEvalRun,
  createPromotionEvent,
} from "../../../../src/control-plane/contract/factories.js";
import type {
  ActuatorType,
  Artifact,
  EvalRun,
  MetricBundle,
  Provenance,
} from "../../../../src/control-plane/contract/types.js";
import { computeTreeHash, type TreeFile } from "../../../../src/control-plane/contract/invariants.js";
import { openRegistry, type Registry } from "../../../../src/control-plane/registry/index.js";
import { attachEvalRun } from "../../../../src/control-plane/eval-ingest/index.js";

const PASSING_METRICS: MetricBundle = {
  quality: { score: 0.95, sampleSize: 2000 },
  cost: { tokensIn: 100, tokensOut: 50 },
  latency: { p50Ms: 10, p95Ms: 20, p99Ms: 30 },
  safety: { regressions: [] },
  evalRunnerIdentity: {
    name: "integration-test",
    version: "1.0.0",
    configHash: ("sha256:" + "9".repeat(64)) as MetricBundle["evalRunnerIdentity"]["configHash"],
  },
};

const BASELINE_PROVENANCE: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T12:00:00.000Z",
};

export interface PayloadSpec {
  /** Map of POSIX path-fragments → file contents inside the payload directory. */
  readonly files: Record<string, string>;
}

/** Materialize a payload directory at <root>/payload-<suffix>/ and return it. */
export function writePayloadDir(root: string, suffix: string, spec: PayloadSpec): string {
  const dir = join(root, `payload-${suffix}`);
  mkdirSync(dir, { recursive: true });
  for (const [rel, content] of Object.entries(spec.files)) {
    const fullPath = join(dir, rel);
    const parent = fullPath.split("/").slice(0, -1).join("/");
    if (parent.length > dir.length) {
      mkdirSync(parent, { recursive: true });
    }
    writeFileSync(fullPath, content, "utf-8");
  }
  return dir;
}

export interface BuildArtifactOptions {
  readonly registry: Registry;
  readonly tmpRoot: string;
  /** Defaults to "grid_ctf". */
  readonly scenario?: string;
  /** Defaults to "prompt-patch". */
  readonly actuatorType?: ActuatorType;
  /** Defaults to "production". */
  readonly env?: string;
  /** Optional payload override. Defaults to a single-prompt prompt-patch payload. */
  readonly payload?: PayloadSpec;
  /** Suffix used to namespace the materialized payload directory. */
  readonly payloadSuffix?: string;
  /** Optional metric overrides; merged on top of the passing baseline. */
  readonly metrics?: Partial<MetricBundle>;
  /** Optional ingestion timestamp (defaults to "2026-04-17T12:30:00.000Z"). */
  readonly ingestedAt?: string;
  /** Optional explicit run id (defaults to "run_<random>"). */
  readonly runId?: string;
  /** Suite id (defaults to "prod-eval"). */
  readonly suiteId?: string;
  /** Provenance override. */
  readonly provenance?: Provenance;
}

export interface BuildArtifactResult {
  readonly artifact: Artifact;
  readonly payloadDir: string;
  readonly evalRun: EvalRun;
}

function defaultPayload(actuatorType: ActuatorType): PayloadSpec {
  switch (actuatorType) {
    case "prompt-patch":
      return { files: { "prompt.txt": "You are a helpful agent.\n" } };
    case "tool-policy":
      return {
        files: {
          "policy.json": JSON.stringify(
            { version: "1", tools: { search: { allow: true } } },
            null,
            2,
          ),
        },
      };
    case "routing-rule":
      return {
        files: {
          "rule.json": JSON.stringify(
            {
              version: "1",
              rules: [{ match: { tool: "search" }, route: "search-fast" }],
            },
            null,
            2,
          ),
        },
      };
    case "fine-tuned-model":
      return {
        files: {
          "pointer.json": JSON.stringify(
            {
              kind: "model-checkpoint",
              externalPath: "s3://example/ckpt-1",
              checkpointHash: "sha256:" + "a".repeat(64),
              family: "test",
              backend: "mlx",
            },
            null,
            2,
          ),
        },
      };
  }
}

/**
 * Register an artifact and attach a passing EvalRun. Returns the artifact +
 * the eval run object (so callers can re-run decidePromotion against it
 * without re-loading from disk).
 */
export async function buildArtifactWithPassingEval(
  opts: BuildArtifactOptions,
): Promise<BuildArtifactResult> {
  const actuatorType: ActuatorType = opts.actuatorType ?? "prompt-patch";
  const payloadSpec = opts.payload ?? defaultPayload(actuatorType);
  const payloadSuffix =
    opts.payloadSuffix ?? Math.random().toString(36).slice(2, 10);

  const payloadDir = writePayloadDir(opts.tmpRoot, payloadSuffix, payloadSpec);

  // Compute payload hash from the in-memory spec (paths are joined POSIX-style
  // for cross-platform parity with hashDirectory).
  const tree: TreeFile[] = Object.entries(payloadSpec.files).map(([rel, content]) => ({
    path: rel,
    content: Buffer.from(content, "utf-8"),
  }));
  const payloadHash = computeTreeHash(tree);

  const scenario: Scenario = parseScenario(opts.scenario ?? "grid_ctf")!;
  const env: EnvironmentTag =
    opts.env !== undefined ? parseEnvironmentTag(opts.env)! : defaultEnvironmentTag();
  const provenance = opts.provenance ?? BASELINE_PROVENANCE;

  const artifact = createArtifact({
    actuatorType,
    scenario,
    environmentTag: env,
    payloadHash,
    provenance,
  });

  opts.registry.saveArtifact(artifact, payloadDir);

  const metrics: MetricBundle = {
    ...PASSING_METRICS,
    ...(opts.metrics ?? {}),
  };
  const suiteId: SuiteId = parseSuiteId(opts.suiteId ?? "prod-eval")!;
  const ingestedAt = opts.ingestedAt ?? "2026-04-17T12:30:00.000Z";
  const runId =
    opts.runId ?? `run_${Math.random().toString(36).slice(2, 10)}`;

  const evalRun = createEvalRun({
    runId,
    artifactId: artifact.id,
    suiteId,
    metrics,
    datasetProvenance: {
      datasetId: "ds-1",
      sliceHash: ("sha256:" + "1".repeat(64)) as MetricBundle["evalRunnerIdentity"]["configHash"],
      sampleCount: metrics.quality.sampleSize,
    },
    ingestedAt,
  });

  const attached = await attachEvalRun(opts.registry, evalRun);

  return { artifact: attached.artifact, payloadDir, evalRun };
}

export interface PromoteOptions {
  readonly registry: Registry;
  readonly artifactId: Artifact["id"];
  readonly to: Artifact["activationState"];
  readonly reason?: string;
  readonly timestamp?: string;
  /** Optional intermediate state; if set, a candidate→intermediate→to chain is performed. */
  readonly via?: Artifact["activationState"];
}

/**
 * Helper: drive the registry through one or two PromotionEvents to reach the
 * desired activation state. Returns the final artifact.
 */
export function promoteArtifact(opts: PromoteOptions): Artifact {
  const reason = opts.reason ?? `promote-to-${opts.to}`;
  const ts0 = opts.timestamp ?? "2026-04-17T12:35:00.000Z";

  const current = opts.registry.loadArtifact(opts.artifactId);
  if (opts.via !== undefined) {
    const ev1 = createPromotionEvent({
      from: current.activationState,
      to: opts.via,
      reason: reason + "-via",
      timestamp: ts0,
    });
    opts.registry.appendPromotionEvent(opts.artifactId, ev1);
    const ev2 = createPromotionEvent({
      from: opts.via,
      to: opts.to,
      reason,
      timestamp: bumpIso(ts0, 1),
    });
    return opts.registry.appendPromotionEvent(opts.artifactId, ev2);
  }
  const ev = createPromotionEvent({
    from: current.activationState,
    to: opts.to,
    reason,
    timestamp: ts0,
  });
  return opts.registry.appendPromotionEvent(opts.artifactId, ev);
}

function bumpIso(iso: string, addSeconds: number): string {
  const d = new Date(iso);
  d.setSeconds(d.getSeconds() + addSeconds);
  return d.toISOString();
}

/**
 * Convenience: open a registry rooted at `tmp` (no implicit side effects).
 */
export function openTestRegistry(tmp: string): Registry {
  return openRegistry(tmp);
}
