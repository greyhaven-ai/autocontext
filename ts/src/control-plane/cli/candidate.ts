// `autoctx candidate ...` subcommand group.
//
// Responsibilities: register / list / show / lineage / rollback for
// control-plane Artifacts. Each command returns a CliResult (stdout/stderr/exitCode)
// so the entry point can print + exit; this keeps the commands testable without
// spawning processes.

import { existsSync, statSync, readdirSync, readFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import type { ActuatorType, ActivationState, Artifact, Provenance } from "../contract/types.js";
import {
  parseArtifactId,
  parseEnvironmentTag,
  parseScenario,
  defaultEnvironmentTag,
  type ArtifactId,
  type EnvironmentTag,
  type Scenario,
} from "../contract/branded-ids.js";
import { createArtifact, createPromotionEvent } from "../contract/factories.js";
import { computeTreeHash, type TreeFile } from "../contract/invariants.js";
import { openRegistry, type ListCandidatesFilter, type Registry } from "../registry/index.js";
import { validateLineageNoCycles } from "../contract/invariants.js";
import { CascadeRollbackRequired } from "../actuators/errors.js";
import { getActuator } from "../actuators/registry.js";
import { PROMPT_PATCH_FILENAME } from "../actuators/prompt-patch/schema.js";
import { TOOL_POLICY_FILENAME } from "../actuators/tool-policy/schema.js";
import { ROUTING_RULE_FILENAME } from "../actuators/routing-rule/schema.js";
import { FINE_TUNED_MODEL_FILENAME } from "../actuators/fine-tuned-model/schema.js";
import { MODEL_ROUTING_FILENAME } from "../actuators/model-routing/schema.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import type { CliResult, CliContext } from "./types.js";

const ACTUATOR_TYPES: readonly ActuatorType[] = [
  "prompt-patch",
  "tool-policy",
  "routing-rule",
  "fine-tuned-model",
  "model-routing",
];

const PAYLOAD_FILE_BY_ACTUATOR: Readonly<Record<ActuatorType, string>> = {
  "prompt-patch": PROMPT_PATCH_FILENAME,
  "tool-policy": TOOL_POLICY_FILENAME,
  "routing-rule": ROUTING_RULE_FILENAME,
  "fine-tuned-model": FINE_TUNED_MODEL_FILENAME,
  "model-routing": MODEL_ROUTING_FILENAME,
};

export const CANDIDATE_HELP_TEXT = `autoctx candidate — manage control-plane candidate artifacts

Subcommands:
  register   Register a new candidate artifact from a payload directory
  list       List candidates (filterable)
  show       Show a single artifact's metadata
  lineage    Print the ancestry DAG of an artifact
  rollback   Roll back an artifact to candidate state

Examples:
  autoctx candidate register --scenario grid_ctf --actuator prompt-patch \\
      --payload ./payload [--parent <id>]... [--env production]
  autoctx candidate list --scenario grid_ctf --output table
  autoctx candidate show <artifactId>
  autoctx candidate lineage <artifactId>
  autoctx candidate rollback <artifactId> --reason "..."
`;

export async function runCandidate(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  const sub = args[0];
  if (!sub || sub === "--help" || sub === "-h") {
    return { stdout: CANDIDATE_HELP_TEXT, stderr: "", exitCode: 0 };
  }
  switch (sub) {
    case "register":
      return runRegister(args.slice(1), ctx);
    case "list":
      return runList(args.slice(1), ctx);
    case "show":
      return runShow(args.slice(1), ctx);
    case "lineage":
      return runLineage(args.slice(1), ctx);
    case "rollback":
      return runRollback(args.slice(1), ctx);
    default:
      return {
        stdout: "",
        stderr: `Unknown candidate subcommand: ${sub}\n${CANDIDATE_HELP_TEXT}`,
        exitCode: EXIT.HARD_FAIL,
      };
  }
}

// ---- register ----

async function runRegister(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const opts = parseFlags(args, {
    scenario: { type: "string", required: true },
    actuator: { type: "string", required: true },
    payload: { type: "string", required: true },
    env: { type: "string" },
    author: { type: "string" },
    output: { type: "string", default: "pretty" },
    parent: { type: "string-array" },
  });
  if ("error" in opts) {
    return { stdout: "", stderr: opts.error, exitCode: EXIT.HARD_FAIL };
  }
  const flags = opts.value;

  const scenario = parseScenario(flags.scenario as string);
  if (scenario === null) {
    return {
      stdout: "",
      stderr: `Invalid scenario: '${flags.scenario as string}' (must match /^[a-z0-9][a-z0-9_-]*$/)`,
      exitCode: EXIT.HARD_FAIL,
    };
  }

  if (!ACTUATOR_TYPES.includes(flags.actuator as ActuatorType)) {
    return {
      stdout: "",
      stderr: `Unknown actuator type: '${flags.actuator as string}'. Valid: ${ACTUATOR_TYPES.join(", ")}`,
      exitCode: EXIT.UNKNOWN_ACTUATOR,
    };
  }

  const payloadAbs = ctx.resolve(flags.payload as string);
  if (!existsSync(payloadAbs) || !statSync(payloadAbs).isDirectory()) {
    return {
      stdout: "",
      stderr: `Payload path does not exist or is not a directory: ${payloadAbs}`,
      exitCode: EXIT.IO_ERROR,
    };
  }

  const payloadError = validateActuatorPayload(flags.actuator as ActuatorType, payloadAbs);
  if (payloadError !== null) {
    return {
      stdout: "",
      stderr: payloadError,
      exitCode: EXIT.VALIDATION_FAILED,
    };
  }

  let env: EnvironmentTag = defaultEnvironmentTag();
  if (flags.env !== undefined) {
    const parsed = parseEnvironmentTag(flags.env as string);
    if (parsed === null) {
      return { stdout: "", stderr: `Invalid env: '${flags.env as string}'`, exitCode: EXIT.HARD_FAIL };
    }
    env = parsed;
  }

  // Parse parents (each must be a valid ArtifactId).
  const parents: ArtifactId[] = [];
  for (const p of (flags.parent as string[] | undefined) ?? []) {
    const parsed = parseArtifactId(p);
    if (parsed === null) {
      return {
        stdout: "",
        stderr: `Invalid parent artifact id: '${p}'`,
        exitCode: EXIT.INVALID_ARTIFACT,
      };
    }
    parents.push(parsed);
  }

  // Compute payload hash.
  const files = collectTree(payloadAbs);
  const payloadHash = computeTreeHash(files);

  const provenance: Provenance = {
    authorType: flags.author !== undefined ? "human" : "autocontext-run",
    authorId: (flags.author as string | undefined) ?? "cli",
    parentArtifactIds: parents,
    createdAt: ctx.now(),
  };

  const artifact = createArtifact({
    actuatorType: flags.actuator as ActuatorType,
    scenario,
    environmentTag: env,
    payloadHash,
    provenance,
  });

  // Lineage cycle check using the registry as the lookup source.
  const registry = openRegistry(ctx.cwd);
  const cycle = validateLineageNoCycles(artifact.id, parents, (id) => {
    try {
      return registry.loadArtifact(id).provenance.parentArtifactIds;
    } catch {
      return null;
    }
  });
  if (!cycle.valid) {
    return {
      stdout: "",
      stderr: cycle.errors.join("; "),
      exitCode: EXIT.INVALID_ARTIFACT,
    };
  }

  try {
    registry.saveArtifact(artifact, payloadAbs);
  } catch (err) {
    return {
      stdout: "",
      stderr: err instanceof Error ? err.message : String(err),
      exitCode: EXIT.IO_ERROR,
    };
  }

  return {
    stdout: formatOutput(artifact, flags.output as OutputMode),
    stderr: "",
    exitCode: EXIT.PASS_STRONG_OR_MODERATE,
  };
}

// ---- list ----

interface MutableFilter {
  scenario?: Scenario;
  environmentTag?: EnvironmentTag;
  actuatorType?: ActuatorType;
  activationState?: ActivationState;
}

async function runList(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const opts = parseFlags(args, {
    scenario: { type: "string" },
    actuator: { type: "string" },
    state: { type: "string" },
    env: { type: "string" },
    output: { type: "string", default: "pretty" },
  });
  if ("error" in opts) {
    return { stdout: "", stderr: opts.error, exitCode: EXIT.HARD_FAIL };
  }
  const flags = opts.value;
  const registry = openRegistry(ctx.cwd);

  const filter: MutableFilter = {};
  if (flags.scenario !== undefined) {
    const s = parseScenario(flags.scenario as string);
    if (s === null) return { stdout: "", stderr: "invalid scenario", exitCode: EXIT.HARD_FAIL };
    filter.scenario = s;
  }
  if (flags.actuator !== undefined) {
    filter.actuatorType = flags.actuator as ActuatorType;
  }
  if (flags.state !== undefined) {
    filter.activationState = flags.state as ActivationState;
  }
  if (flags.env !== undefined) {
    const e = parseEnvironmentTag(flags.env as string);
    if (e === null) return { stdout: "", stderr: "invalid env", exitCode: EXIT.HARD_FAIL };
    filter.environmentTag = e;
  }

  const list = registry.listCandidates(filter as ListCandidatesFilter);
  // Compact list rows for readability.
  const rows = list.map((a) => ({
    id: a.id,
    actuatorType: a.actuatorType,
    scenario: a.scenario,
    environmentTag: a.environmentTag,
    activationState: a.activationState,
    parents: a.provenance.parentArtifactIds.length,
    evalRuns: a.evalRuns.length,
  }));
  return {
    stdout: formatOutput(rows, flags.output as OutputMode),
    stderr: "",
    exitCode: EXIT.PASS_STRONG_OR_MODERATE,
  };
}

// ---- show ----

async function runShow(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const id = args[0];
  if (!id || id.startsWith("--")) {
    return { stdout: "", stderr: "Usage: autoctx candidate show <artifactId>", exitCode: EXIT.HARD_FAIL };
  }
  const parsed = parseArtifactId(id);
  if (parsed === null) {
    return { stdout: "", stderr: `Invalid artifact id: ${id}`, exitCode: EXIT.INVALID_ARTIFACT };
  }
  const flags = parseFlags(args.slice(1), { output: { type: "string", default: "pretty" } });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.HARD_FAIL };
  }

  const registry = openRegistry(ctx.cwd);
  let artifact: Artifact;
  try {
    artifact = registry.loadArtifact(parsed);
  } catch (err) {
    return {
      stdout: "",
      stderr: err instanceof Error ? err.message : String(err),
      exitCode: EXIT.INVALID_ARTIFACT,
    };
  }
  return {
    stdout: formatOutput(artifact, flags.value.output as OutputMode),
    stderr: "",
    exitCode: EXIT.PASS_STRONG_OR_MODERATE,
  };
}

// ---- lineage ----

async function runLineage(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const id = args[0];
  if (!id || id.startsWith("--")) {
    return { stdout: "", stderr: "Usage: autoctx candidate lineage <artifactId>", exitCode: EXIT.HARD_FAIL };
  }
  const parsed = parseArtifactId(id);
  if (parsed === null) {
    return { stdout: "", stderr: `Invalid artifact id: ${id}`, exitCode: EXIT.INVALID_ARTIFACT };
  }
  const registry = openRegistry(ctx.cwd);

  const load = (aid: ArtifactId): Artifact | null => {
    try {
      return registry.loadArtifact(aid);
    } catch {
      return null;
    }
  };

  const root = load(parsed);
  if (root === null) {
    return { stdout: "", stderr: `Artifact not found: ${parsed}`, exitCode: EXIT.INVALID_ARTIFACT };
  }

  const lines: string[] = [];
  function walk(aid: ArtifactId, indent: number, seen: Set<ArtifactId>): void {
    if (seen.has(aid)) {
      lines.push(`${"  ".repeat(indent)}- ${aid} (cycle)`);
      return;
    }
    seen.add(aid);
    const a = load(aid);
    if (!a) {
      lines.push(`${"  ".repeat(indent)}- ${aid} (missing)`);
      return;
    }
    lines.push(`${"  ".repeat(indent)}- ${aid} [${a.actuatorType}/${a.scenario}/${a.activationState}]`);
    for (const p of a.provenance.parentArtifactIds) {
      walk(p, indent + 1, seen);
    }
  }
  walk(parsed, 0, new Set());
  return {
    stdout: lines.join("\n"),
    stderr: "",
    exitCode: EXIT.PASS_STRONG_OR_MODERATE,
  };
}

// ---- rollback ----

/**
 * Cascade-rollback precheck. For an actuator whose rollback strategy is
 * `cascade-set` (currently only routing-rule), look up the dependsOn list and
 * find any artifacts of those types that are still in an "active" state in
 * the same (scenario, environmentTag) tuple. If any are found, return them
 * so the caller can refuse the rollback with CascadeRollbackRequired.
 *
 * v1 simulates the cross-actuator dependency at the registry level (rather
 * than parsing the routing-rule payload for explicit references) per spec
 * §10.3 Flow 5 implementation note.
 */
function findIncompatibleDependents(
  registry: Registry,
  candidate: Artifact,
): readonly ArtifactId[] {
  const reg = getActuator(candidate.actuatorType);
  if (reg === null) return [];
  if (reg.rollback.kind !== "cascade-set") return [];

  const dependsOn = reg.rollback.dependsOn;
  const dependents: ArtifactId[] = [];
  for (const depType of dependsOn) {
    const matches = registry.listCandidates({
      scenario: candidate.scenario,
      environmentTag: candidate.environmentTag,
      actuatorType: depType,
      activationState: "active",
    });
    for (const m of matches) {
      dependents.push(m.id);
    }
  }
  return dependents;
}

async function runRollback(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const id = args[0];
  if (!id || id.startsWith("--")) {
    return { stdout: "", stderr: "Usage: autoctx candidate rollback <artifactId> --reason \"...\"", exitCode: EXIT.HARD_FAIL };
  }
  const parsed = parseArtifactId(id);
  if (parsed === null) {
    return { stdout: "", stderr: `Invalid artifact id: ${id}`, exitCode: EXIT.INVALID_ARTIFACT };
  }
  const flags = parseFlags(args.slice(1), { reason: { type: "string", required: true } });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.HARD_FAIL };
  }

  const registry = openRegistry(ctx.cwd);
  let current: Artifact;
  try {
    current = registry.loadArtifact(parsed);
  } catch (err) {
    return {
      stdout: "",
      stderr: err instanceof Error ? err.message : String(err),
      exitCode: EXIT.INVALID_ARTIFACT,
    };
  }

  // Cascade precheck — refuse rollback BEFORE mutating state if any
  // active dependents would be left in an incompatible state.
  const incompatible = findIncompatibleDependents(registry, current);
  if (incompatible.length > 0) {
    return {
      stdout: "",
      stderr: `CascadeRollbackRequired: dependents must be rolled back first: ${incompatible.join(", ")}`,
      exitCode: EXIT.CASCADE_ROLLBACK_REQUIRED,
    };
  }

  const event = createPromotionEvent({
    from: current.activationState,
    to: "candidate",
    reason: flags.value.reason as string,
    timestamp: ctx.now(),
  });

  try {
    const updated = registry.appendPromotionEvent(parsed, event);
    return {
      stdout: `Rolled back ${updated.id} to candidate`,
      stderr: "",
      exitCode: EXIT.PASS_STRONG_OR_MODERATE,
    };
  } catch (err) {
    if (err instanceof CascadeRollbackRequired) {
      return {
        stdout: "",
        stderr: `CascadeRollbackRequired: dependents must be rolled back first: ${err.dependents.join(", ")}`,
        exitCode: EXIT.CASCADE_ROLLBACK_REQUIRED,
      };
    }
    return {
      stdout: "",
      stderr: err instanceof Error ? err.message : String(err),
      exitCode: EXIT.HARD_FAIL,
    };
  }
}

// ---- helpers ----

interface FlagSpec {
  type: "string" | "string-array";
  required?: boolean;
  default?: string;
}

interface ParsedFlags {
  [key: string]: string | string[] | undefined;
}

type FlagsResult =
  | { value: ParsedFlags }
  | { error: string };

function parseFlags(args: readonly string[], spec: Record<string, FlagSpec>): FlagsResult {
  const parsed: ParsedFlags = {};
  for (let i = 0; i < args.length; i++) {
    const a = args[i]!;
    if (!a.startsWith("--")) continue;
    const name = a.slice(2);
    if (!(name in spec)) {
      return { error: `Unknown flag: --${name}` };
    }
    const s = spec[name]!;
    const next = args[i + 1];
    if (next === undefined || next.startsWith("--")) {
      return { error: `Flag --${name} requires a value` };
    }
    if (s.type === "string-array") {
      const prior = parsed[name];
      const arr = Array.isArray(prior) ? prior : [];
      arr.push(next);
      parsed[name] = arr;
    } else {
      parsed[name] = next;
    }
    i += 1;
  }

  for (const [key, s] of Object.entries(spec)) {
    const v = parsed[key];
    if (v === undefined) {
      if (s.default !== undefined) {
        parsed[key] = s.default;
      } else if (s.required) {
        return { error: `Missing required flag: --${key}` };
      }
    }
  }
  return { value: parsed };
}

function collectTree(root: string): TreeFile[] {
  const out: TreeFile[] = [];
  function walk(d: string) {
    for (const entry of readdirSync(d)) {
      const full = join(d, entry);
      if (statSync(full).isDirectory()) {
        walk(full);
      } else {
        const rel = relative(root, full).split(sep).join("/");
        out.push({ path: rel, content: readFileSync(full) });
      }
    }
  }
  walk(root);
  return out;
}

function validateActuatorPayload(actuatorType: ActuatorType, payloadAbs: string): string | null {
  const reg = getActuator(actuatorType);
  if (reg === null) {
    return `No actuator registered for type: ${actuatorType}`;
  }
  const fileName = PAYLOAD_FILE_BY_ACTUATOR[actuatorType];
  const filePath = join(payloadAbs, fileName);
  if (!existsSync(filePath) || !statSync(filePath).isFile()) {
    return `Payload for ${actuatorType} must include ${fileName}`;
  }

  let raw: unknown;
  try {
    if (actuatorType === "prompt-patch") {
      raw = readFileSync(filePath, "utf-8");
    } else {
      raw = JSON.parse(readFileSync(filePath, "utf-8"));
    }
  } catch (err) {
    return `Invalid ${actuatorType} payload in ${fileName}: ${err instanceof Error ? err.message : String(err)}`;
  }

  try {
    reg.actuator.parsePayload(raw);
  } catch (err) {
    return `Invalid ${actuatorType} payload in ${fileName}: ${err instanceof Error ? err.message : String(err)}`;
  }

  return null;
}
