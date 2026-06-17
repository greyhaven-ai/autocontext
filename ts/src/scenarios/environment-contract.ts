import type { ScenarioInterface } from "./game-interface.js";

export const SCENARIO_ENVIRONMENT_HOOK_KINDS = [
  "setup",
  "reset",
  "rollout",
  "verification",
  "scoring",
  "replay",
  "evidence",
  "cleanup",
] as const;

export type ScenarioEnvironmentHookKind = (typeof SCENARIO_ENVIRONMENT_HOOK_KINDS)[number];

export interface ScenarioEnvironmentHook {
  kind: ScenarioEnvironmentHookKind;
  label: string;
  description: string;
  required: boolean;
  inputs: string[];
  emits: string[];
  evidence_refs: string[];
}

export type ScenarioEnvironmentHooks = Record<
  ScenarioEnvironmentHookKind,
  ScenarioEnvironmentHook[]
>;

export interface ScenarioEnvironmentContract {
  schema_version: 1;
  scenario_name: string;
  scenario_family: string;
  hooks: ScenarioEnvironmentHooks;
}

export const ScenarioEnvironmentContractSchema = {
  parse(value: unknown): ScenarioEnvironmentContract {
    const contract = asRecord(value, "contract");
    rejectExtra(contract, ["schema_version", "scenario_name", "scenario_family", "hooks"]);
    if (contract.schema_version !== 1) throw new Error("schema_version must be 1");
    const hooks = asRecord(contract.hooks, "hooks");
    rejectExtra(hooks, [...SCENARIO_ENVIRONMENT_HOOK_KINDS]);
    return {
      schema_version: 1,
      scenario_name: asString(contract.scenario_name, "scenario_name"),
      scenario_family: asString(contract.scenario_family, "scenario_family"),
      hooks: Object.fromEntries(
        SCENARIO_ENVIRONMENT_HOOK_KINDS.map((kind) => [kind, parseHooks(hooks[kind], kind)]),
      ) as ScenarioEnvironmentHooks,
    };
  },
} as const;

function asRecord(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function asString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) throw new Error(`${label} must be a string`);
  return value;
}

function stringList(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error(`${label} must be a string array`);
  }
  return [...value];
}

function rejectExtra(record: Record<string, unknown>, allowed: readonly string[]): void {
  const allowedSet = new Set(allowed);
  const extra = Object.keys(record).filter((key) => !allowedSet.has(key));
  if (extra.length > 0) throw new Error(`unexpected field(s): ${extra.sort().join(", ")}`);
}

function parseHooks(value: unknown, kind: ScenarioEnvironmentHookKind): ScenarioEnvironmentHook[] {
  if (!Array.isArray(value) || value.length === 0)
    throw new Error(`${kind} hooks must be a non-empty array`);
  return value.map((item) => parseHook(item, kind));
}

function parseHook(
  value: unknown,
  expectedKind: ScenarioEnvironmentHookKind,
): ScenarioEnvironmentHook {
  const hookValue = asRecord(value, expectedKind);
  rejectExtra(hookValue, [
    "kind",
    "label",
    "description",
    "required",
    "inputs",
    "emits",
    "evidence_refs",
  ]);
  if (hookValue.kind !== expectedKind) throw new Error(`expected ${expectedKind} hook`);
  if (typeof hookValue.required !== "boolean") throw new Error("required must be boolean");
  return {
    kind: expectedKind,
    label: asString(hookValue.label, "label"),
    description: asString(hookValue.description, "description"),
    required: hookValue.required,
    inputs: stringList(hookValue.inputs, "inputs"),
    emits: stringList(hookValue.emits, "emits"),
    evidence_refs: stringList(hookValue.evidence_refs, "evidence_refs"),
  };
}

function hook(
  kind: ScenarioEnvironmentHookKind,
  label: string,
  description: string,
  opts: Partial<Pick<ScenarioEnvironmentHook, "inputs" | "emits" | "evidence_refs">> = {},
): ScenarioEnvironmentHook {
  return {
    kind,
    label,
    description,
    required: true,
    inputs: opts.inputs ?? [],
    emits: opts.emits ?? [],
    evidence_refs: opts.evidence_refs ?? [],
  };
}

export function agentTaskTemplateEnvironmentContract(templateName: string): ScenarioEnvironmentContract {
  return {
    schema_version: 1,
    scenario_name: templateName,
    scenario_family: "agent_task",
    hooks: {
      setup: [hook("setup", "template task load", "Load prompt and rubric.", {
        emits: ["task_prompt", "judge_rubric"],
      })],
      reset: [hook("reset", "seeded task state", "Create clean task state.", {
        inputs: ["seed"],
        emits: ["state"],
      })],
      rollout: [hook("rollout", "agent output attempt", "Produce one task output.", {
        inputs: ["task_prompt"],
        emits: ["agent_output"],
      })],
      verification: [hook("verification", "judge rubric check", "Judge the output.", {
        inputs: ["agent_output", "judge_rubric"],
        emits: ["judge_result"],
        evidence_refs: ["AgentTaskResult.reasoning"],
      })],
      scoring: [hook("scoring", "judge scalar score", "Emit scalar score.", {
        inputs: ["judge_result"],
        emits: ["scalar_score"],
      })],
      replay: [hook("replay", "attempt transcript", "Keep prompt, output, and feedback.", {
        inputs: ["task_prompt", "agent_output", "judge_result"],
        emits: ["attempt_transcript"],
      })],
      evidence: [hook("evidence", "judge feedback evidence", "Keep judge reasoning and dimensions.", {
        inputs: ["judge_result"],
        emits: ["judge_reasoning", "dimension_scores"],
      })],
      cleanup: [hook("cleanup", "stateless template cleanup", "No external mutable state.", {
        emits: ["no_external_state"],
      })],
    },
  };
}

export function scenarioEnvironmentContractForGame(
  scenario: Pick<ScenarioInterface, "name">,
  scenarioFamily = "game",
): ScenarioEnvironmentContract {
  return {
    schema_version: 1,
    scenario_name: scenario.name,
    scenario_family: scenarioFamily,
    hooks: {
      setup: [
        hook(
          "setup",
          "seeded initial state",
          "initialState(seed) creates the deterministic harness state.",
          {
            inputs: ["seed"],
            emits: ["state"],
          },
        ),
      ],
      reset: [
        hook(
          "reset",
          "repeatable reset",
          "Calling initialState(seed) again restores a clean state for replay.",
          {
            inputs: ["seed"],
            emits: ["state"],
          },
        ),
      ],
      rollout: [
        hook(
          "rollout",
          "strategy rollout",
          "validateActions and step execute a candidate strategy in the harness.",
          {
            inputs: ["state", "strategy"],
            emits: ["next_state"],
          },
        ),
      ],
      verification: [
        hook(
          "verification",
          "action and terminal checks",
          "validateActions, isTerminal, and getResult reject invalid or incomplete runs.",
          {
            inputs: ["state", "strategy"],
            emits: ["validation_errors", "terminal_state"],
            evidence_refs: ["Result.validationErrors"],
          },
        ),
      ],
      scoring: [
        hook(
          "scoring",
          "scalar result score",
          "getResult emits the scalar score consumed by tournaments and reports.",
          {
            inputs: ["terminal_state"],
            emits: ["scalar_score"],
          },
        ),
      ],
      replay: [
        hook(
          "replay",
          "replay timeline",
          "Result.replay and replayToNarrative preserve the run trace for inspection.",
          {
            inputs: ["result.replay"],
            emits: ["replay_timeline"],
          },
        ),
      ],
      evidence: [
        hook(
          "evidence",
          "metrics and validation evidence",
          "Result.summary, Result.metrics, and validation errors explain the score.",
          {
            inputs: ["result"],
            emits: ["summary", "metrics", "validation_errors"],
          },
        ),
      ],
      cleanup: [
        hook(
          "cleanup",
          "in-memory cleanup",
          "Default game scenarios do not retain external resources between seeded runs.",
          {
            emits: ["no_external_state"],
          },
        ),
      ],
    },
  };
}
