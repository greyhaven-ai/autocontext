# autocontext TypeScript Full Port Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the complete autocontext generation loop, agent orchestration, game scenarios, knowledge system, interactive server, and TUI into the `autoctx` npm package — making it a standalone, fully-featured evaluation harness runnable via `npx autoctx run` or `npx autoctx tui` with no Python dependency.

**Architecture:** The TS port already has ~40% of primitives (judge, improvement loop, storage, providers, runtimes, scenarios families, knowledge packages, RLM, MCP). This plan adds the generation loop core (orchestrator, tournament, backpressure, knowledge persistence), game scenario interfaces (grid_ctf), the interactive WebSocket server, and the Ink TUI — all bundled into a single npm package. secure-exec replaces pydantic-monty for sandboxed code execution. Local model inference uses OpenAI-compatible endpoints (mlx_lm.server, Ollama) via the existing provider.

**Tech Stack:** TypeScript 5.7+, Node.js 18+, better-sqlite3, Zod, ws (WebSocket), secure-exec (sandbox), Ink 5 + React 18 (TUI), vitest (tests)

**Reference:** Python implementation at `autocontext/src/autocontext/` is the source of truth for algorithms and data flow.

---

## Inventory: What Exists vs What's Needed

### Already Implemented in TS (`ts/src/`)

| Module | Status | Key Exports |
|--------|--------|-------------|
| `types/` | Complete | Zod schemas, LLMProvider, AgentTaskInterface, JudgeResult |
| `judge/` | Complete | LLMJudge, 4-tier parse, rubric coherence, dimension pinning |
| `providers/` | Complete | Anthropic, OpenAI-compat, Ollama, vLLM (pure fetch) |
| `runtimes/` | Partial | DirectAPI, ClaudeCLI (missing: CodexCLI) |
| `storage/` | Partial | SQLiteStore for task queue + human feedback (missing: run/gen/match tables) |
| `execution/` | Complete | ImprovementLoop, TaskRunner, StrategyValidator, ActionFilter, HarnessLoader |
| `scenarios/` | Partial | 11 agent-task families with designer+spec+creator (missing: ScenarioInterface for games, grid_ctf, registry) |
| `knowledge/` | Partial | SkillPackage, HarnessStore (missing: playbook versioning, trajectory, search) |
| `loop/` | Partial | HypothesisTree with Thompson sampling (missing: generation runner, backpressure, controller, events) |
| `mcp/` | Complete | 5 tools on stdio |
| `rlm/` | Complete | RlmSession, extractCode, types |
| `cli/` | Partial | judge, improve, queue, status, serve (missing: run, tui) |

### Must Build

| Module | Priority | Description |
|--------|----------|-------------|
| `config/` | P0 | Full AppSettings with env var loading, presets |
| `storage/` extensions | P0 | Run, generation, match, agent_output tables |
| `loop/events.ts` | P0 | NDJSON event stream emitter |
| `loop/controller.ts` | P0 | Pause/resume state machine |
| `scenarios/game-interface.ts` | P0 | ScenarioInterface ABC for tournament games |
| `scenarios/grid-ctf.ts` | P0 | Grid CTF game scenario |
| `scenarios/registry.ts` | P0 | SCENARIO_REGISTRY + dual-interface dispatch |
| `execution/elo.ts` | P0 | Elo rating calculation |
| `execution/tournament.ts` | P0 | Match execution + scoring |
| `knowledge/playbook.ts` | P0 | Versioned playbook with rollback |
| `knowledge/trajectory.ts` | P0 | Score trajectory table builder |
| `prompts/` | P0 | Prompt template assembly + context budget |
| `agents/` | P0 | Orchestrator, roles, provider bridge, model router |
| `loop/backpressure.ts` | P0 | advance/retry/rollback gate |
| `loop/generation-runner.ts` | P0 | Core generation loop |
| `runtimes/codex-cli.ts` | P1 | Codex CLI runtime |
| `server/` | P1 | WebSocket server + protocol + run manager |
| `tui/` | P1 | Ink terminal UI (move from `tui/`) |
| `execution/sandbox.ts` | P2 | secure-exec integration |
| `loop/ecosystem-runner.ts` | P2 | Multi-provider cycling |
| `agents/curator.ts` | P2 | Quality gate + lesson consolidation |

---

## File Structure (New & Modified Files)

```
ts/src/
  config/
    settings.ts              # NEW — AppSettings Zod schema + env var loader
    presets.ts               # NEW — Preset overrides (quick, thorough, etc.)
  loop/
    generation-runner.ts     # NEW — Core generation loop
    backpressure.ts          # NEW — Gate logic (advance/retry/rollback)
    controller.ts            # NEW — Pause/resume state machine
    events.ts                # NEW — NDJSON event stream emitter
    ecosystem-runner.ts      # NEW (P2) — Multi-provider cycling
    hypothesis-tree.ts       # EXISTS — Already complete
    index.ts                 # MODIFY — Re-export new modules
  agents/
    orchestrator.ts          # NEW — 6-role agent dispatch (competitor→translator→analyst/coach/architect→curator)
    roles.ts                 # NEW — Role definitions, output parsing, markers
    provider-bridge.ts       # NEW — Runtime→Client adapter, RetryProvider wrapper
    model-router.ts          # NEW — Tier-based model selection
    index.ts                 # NEW — Barrel export
  prompts/
    templates.ts             # NEW — Prompt assembly (PromptBundle)
    context-budget.ts        # NEW — Token budget allocator
    index.ts                 # NEW — Barrel export
  scenarios/
    game-interface.ts        # NEW — ScenarioInterface ABC (multi-step state machine)
    grid-ctf.ts              # NEW — Grid CTF game logic
    registry.ts              # NEW — SCENARIO_REGISTRY + detect_family
    families.ts              # EXISTS — Add ScenarioFamily types
    index.ts                 # MODIFY — Re-export new modules
  knowledge/
    playbook.ts              # NEW — Versioned playbook read/write/rollback
    artifact-store.ts        # NEW — Full artifact persistence (tools, snapshots, lessons, hints, skills)
    trajectory.ts            # NEW — Score trajectory markdown table
    search.ts                # NEW — TF-IDF strategy search
    index.ts                 # MODIFY — Re-export new modules
  execution/
    elo.ts                   # NEW — Elo expected score + update
    supervisor.ts            # NEW — Executor abstraction (local, sandbox, future remote)
    tournament.ts            # NEW — Run N matches, aggregate scores
    supervisor.ts            # NEW — Execution supervisor (dispatch to executor)
    sandbox.ts               # NEW (P2) — secure-exec integration
    index.ts                 # MODIFY — Re-export new modules
  storage/
    index.ts                 # MODIFY — Add run/gen/match/agent_output methods
  runtimes/
    codex-cli.ts             # NEW — Codex CLI runtime
    index.ts                 # MODIFY — Re-export CodexCLIRuntime
  server/
    ws-server.ts             # NEW — WebSocket server (ws library)
    protocol.ts              # NEW — Message types (Zod, mirrors Python)
    run-manager.ts           # NEW — Run lifecycle management
    index.ts                 # NEW — Barrel export
  tui/
    index.tsx                # MOVE from tui/src/index.tsx
    App.tsx                  # MOVE from tui/src/App.tsx
    components/              # MOVE from tui/src/components/
    hooks/                   # MOVE from tui/src/hooks/
    protocol.ts              # MOVE from tui/src/protocol.ts
    types.ts                 # MOVE from tui/src/types.ts
  cli/
    index.ts                 # MODIFY — Add run, tui commands
  index.ts                   # MODIFY — Re-export new modules

ts/migrations/
  009-runs-generations.sql   # NEW — Run + generation + match + agent_output tables
```

---

## Phase 1: Foundation (Tasks 1–4)

### Task 1: Config/Settings System

**Files:**
- Create: `ts/src/config/settings.ts`
- Create: `ts/src/config/presets.ts`
- Create: `ts/src/config/index.ts`
- Test: `ts/tests/config.test.ts`

**Python reference:** `autocontext/src/autocontext/config/settings.py`

This is the env-var-driven configuration that controls every aspect of the harness.

- [ ] **Step 1: Write failing test for settings loading**

```typescript
// ts/tests/config.test.ts
import { describe, it, expect, afterEach } from "vitest";
import { loadSettings } from "../src/config/settings.js";

describe("AppSettings", () => {
  const saved = { ...process.env };
  afterEach(() => {
    // Restore env vars
    for (const key of Object.keys(process.env)) {
      if (key.startsWith("AUTOCONTEXT_")) delete process.env[key];
    }
    Object.assign(process.env, saved);
  });

  it("loads defaults when no env vars set", () => {
    const s = loadSettings();
    expect(s.agentProvider).toBe("anthropic");
    expect(s.matchesPerGeneration).toBe(3);
    expect(s.backpressureMinDelta).toBe(0.005);
    expect(s.curatorEnabled).toBe(true);
    expect(s.dbPath).toBe("runs/autocontext.sqlite3");
  });

  it("reads AUTOCONTEXT_ env vars", () => {
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "claude-cli";
    process.env.AUTOCONTEXT_MATCHES_PER_GENERATION = "5";
    process.env.AUTOCONTEXT_CURATOR_ENABLED = "false";
    const s = loadSettings();
    expect(s.agentProvider).toBe("claude-cli");
    expect(s.matchesPerGeneration).toBe(5);
    expect(s.curatorEnabled).toBe(false);
  });

  it("applies preset overrides", () => {
    process.env.AUTOCONTEXT_PRESET = "quick";
    const s = loadSettings();
    expect(s.matchesPerGeneration).toBe(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ts && npx vitest run tests/config.test.ts`
Expected: FAIL — module not found

- [ ] **Step 3: Implement AppSettings schema with Zod + env var loader**

```typescript
// ts/src/config/settings.ts
import { z } from "zod";

const boolCoerce = z.preprocess(
  (v) => (typeof v === "string" ? v === "true" || v === "1" : v),
  z.boolean(),
);

export const AppSettingsSchema = z.object({
  dbPath: z.string().default("runs/autocontext.sqlite3"),
  runsRoot: z.string().default("runs"),
  knowledgeRoot: z.string().default("knowledge"),
  skillsRoot: z.string().default("skills"),
  executorMode: z.string().default("local"),
  agentProvider: z.string().default("anthropic"),
  anthropicApiKey: z.string().optional(),

  // Per-role models
  modelCompetitor: z.string().default("claude-sonnet-4-5-20250929"),
  modelAnalyst: z.string().default("claude-sonnet-4-5-20250929"),
  modelCoach: z.string().default("claude-opus-4-6"),
  modelArchitect: z.string().default("claude-opus-4-6"),
  modelTranslator: z.string().default("claude-sonnet-4-5-20250929"),
  modelCurator: z.string().default("claude-opus-4-6"),

  // Loop tuning
  architectEveryNGens: z.coerce.number().int().min(1).default(3),
  matchesPerGeneration: z.coerce.number().int().min(1).default(3),
  backpressureMinDelta: z.coerce.number().default(0.005),
  backpressureMode: z.string().default("simple"),
  backpressurePlateauWindow: z.coerce.number().int().min(1).default(3),
  backpressurePlateauRelaxation: z.coerce.number().min(0).max(1).default(0.5),
  defaultGenerations: z.coerce.number().int().min(1).default(1),
  seedBase: z.coerce.number().int().default(1000),
  maxRetries: z.coerce.number().int().min(0).default(2),
  retryBackoffSeconds: z.coerce.number().min(0).default(0.25),
  eventStreamPath: z.string().default("runs/events.ndjson"),

  // Curator
  curatorEnabled: boolCoerce.default(true),
  curatorConsolidateEveryNGens: z.coerce.number().int().min(1).default(3),
  skillMaxLessons: z.coerce.number().int().min(1).default(30),

  // Knowledge
  crossRunInheritance: boolCoerce.default(true),
  playbookMaxVersions: z.coerce.number().int().min(1).default(5),
  contextBudgetTokens: z.coerce.number().int().min(0).default(100_000),

  // RLM
  rlmEnabled: boolCoerce.default(false),
  rlmMaxTurns: z.coerce.number().int().min(1).max(50).default(25),
  rlmSubModel: z.string().default("claude-haiku-4-5-20251001"),
  rlmBackend: z.string().default("exec"),

  // Judge
  judgeModel: z.string().default("claude-sonnet-4-20250514"),
  judgeSamples: z.coerce.number().int().min(1).default(1),
  judgeTemperature: z.coerce.number().min(0).default(0.0),
  judgeProvider: z.string().default("anthropic"),
  judgeBaseUrl: z.string().optional(),
  judgeApiKey: z.string().optional(),

  // Per-role provider overrides
  competitorProvider: z.string().default(""),
  analystProvider: z.string().default(""),
  coachProvider: z.string().default(""),
  architectProvider: z.string().default(""),

  // Tier routing
  tierRoutingEnabled: boolCoerce.default(false),
  tierHaikuModel: z.string().default("claude-haiku-4-5-20251001"),
  tierSonnetModel: z.string().default("claude-sonnet-4-5-20250929"),
  tierOpusModel: z.string().default("claude-opus-4-6"),
  tierCompetitorHaikuMaxGen: z.coerce.number().int().min(1).default(3),

  // Code strategies
  codeStrategiesEnabled: boolCoerce.default(false),

  // Pre-validation
  prevalidationEnabled: boolCoerce.default(false),
  prevalidationMaxRetries: z.coerce.number().int().min(0).max(5).default(2),

  // Harness
  harnessValidatorsEnabled: boolCoerce.default(false),
  harnessMode: z.enum(["none", "filter", "verify", "policy"]).default("none"),
  harnessTimeoutSeconds: z.coerce.number().min(0.5).max(60).default(5.0),

  // Claude CLI runtime
  claudeModel: z.string().default("sonnet"),
  claudeTimeout: z.coerce.number().min(1).default(120.0),
  claudeTools: z.string().optional(),
  claudePermissionMode: z.string().default("bypassPermissions"),

  // Codex CLI runtime
  codexModel: z.string().default("o4-mini"),
  codexTimeout: z.coerce.number().min(1).default(120.0),
  codexApprovalMode: z.string().default("full-auto"),

  // Progress + constraints
  progressJsonEnabled: boolCoerce.default(true),
  constraintPromptsEnabled: boolCoerce.default(true),

  // Stagnation
  stagnationResetEnabled: boolCoerce.default(false),
  stagnationRollbackThreshold: z.coerce.number().int().min(1).default(5),

  // Cost tracking
  costTrackingEnabled: boolCoerce.default(true),
  costBudgetLimit: z.coerce.number().optional(),

  // Exploration
  explorationMode: z.enum(["linear", "rapid", "tree"]).default("linear"),
});

export type AppSettings = z.infer<typeof AppSettingsSchema>;

/** Convert AUTOCONTEXT_SOME_FIELD → someField */
function envKeyToField(envKey: string): string {
  const stripped = envKey.replace(/^AUTOCONTEXT_/, "");
  return stripped
    .toLowerCase()
    .replace(/_([a-z])/g, (_, c) => c.toUpperCase());
}

// Note: use static import at top of file, not require()
// import { applyPreset } from "./presets.js";

export function loadSettings(): AppSettings {
  const presetName = process.env.AUTOCONTEXT_PRESET ?? "";
  const preset = applyPreset(presetName);

  const raw: Record<string, unknown> = { ...preset };

  // Scan env vars for AUTOCONTEXT_* overrides
  for (const [key, value] of Object.entries(process.env)) {
    if (key.startsWith("AUTOCONTEXT_") && value !== undefined) {
      const field = envKeyToField(key);
      if (field in AppSettingsSchema.shape) {
        raw[field] = value;
      }
    }
  }

  return AppSettingsSchema.parse(raw);
}
```

```typescript
// ts/src/config/presets.ts
const PRESETS: Record<string, Record<string, unknown>> = {
  quick: {
    matchesPerGeneration: 1,
    maxRetries: 0,
    curatorEnabled: false,
    architectEveryNGens: 999,
  },
  thorough: {
    matchesPerGeneration: 5,
    maxRetries: 3,
    curatorConsolidateEveryNGens: 2,
  },
};

export function applyPreset(name: string): Record<string, unknown> {
  if (!name) return {};
  return PRESETS[name] ?? {};
}
```

```typescript
// ts/src/config/index.ts
export { AppSettings, AppSettingsSchema, loadSettings } from "./settings.js";
export { applyPreset } from "./presets.js";
```

- [ ] **Step 4: Run tests**

Run: `cd ts && npx vitest run tests/config.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ts/src/config/ ts/tests/config.test.ts
git commit -m "feat(ts): add config/settings system with env var loading and presets"
```

---

### Task 2: Storage Extensions (Run/Generation/Match Tables)

**Files:**
- Create: `ts/migrations/009-runs-generations.sql`
- Modify: `ts/src/storage/index.ts`
- Test: `ts/tests/storage-runs.test.ts`

**Python reference:** `autocontext/src/autocontext/storage/sqlite_store.py` — the `create_run`, `upsert_generation`, `record_match`, `append_agent_output` methods.

The existing SQLiteStore only handles the task queue. The generation loop needs run tracking.

- [ ] **Step 1: Write the migration SQL**

```sql
-- ts/migrations/009-runs-generations.sql
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  scenario TEXT NOT NULL,
  generations INTEGER NOT NULL DEFAULT 1,
  mode TEXT NOT NULL DEFAULT 'generation',
  agent_provider TEXT NOT NULL DEFAULT 'anthropic',
  status TEXT NOT NULL DEFAULT 'running',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS generations (
  run_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  mean_score REAL NOT NULL DEFAULT 0.0,
  best_score REAL NOT NULL DEFAULT 0.0,
  elo REAL NOT NULL DEFAULT 0.0,
  wins INTEGER NOT NULL DEFAULT 0,
  losses INTEGER NOT NULL DEFAULT 0,
  gate_decision TEXT NOT NULL DEFAULT 'pending',
  status TEXT NOT NULL DEFAULT 'pending',
  duration_seconds REAL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (run_id, generation),
  FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  match_index INTEGER NOT NULL,
  score REAL NOT NULL,
  seed INTEGER,
  replay_json TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS agent_outputs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
```

- [ ] **Step 2: Write failing tests for new storage methods**

```typescript
// ts/tests/storage-runs.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { SQLiteStore } from "../src/storage/index.js";
import { join } from "node:path";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";

describe("SQLiteStore — runs & generations", () => {
  let store: SQLiteStore;
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "autoctx-test-"));
    store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
  });

  afterEach(() => {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("creates and retrieves a run", () => {
    store.createRun("run1", "grid_ctf", 3, "generation", "anthropic");
    const run = store.getRun("run1");
    expect(run).toBeDefined();
    expect(run!.scenario).toBe("grid_ctf");
    expect(run!.status).toBe("running");
  });

  it("upserts generation data", () => {
    store.createRun("run1", "grid_ctf", 3, "generation", "anthropic");
    store.upsertGeneration("run1", 1, {
      meanScore: 0.65,
      bestScore: 0.78,
      elo: 1050,
      wins: 2,
      losses: 1,
      gateDecision: "advance",
      status: "completed",
    });
    const gen = store.getGeneration("run1", 1);
    expect(gen!.bestScore).toBe(0.78);
    expect(gen!.gateDecision).toBe("advance");
  });

  it("records match results", () => {
    store.createRun("run1", "grid_ctf", 1, "generation", "anthropic");
    store.recordMatch("run1", 1, 0, 0.85, 42);
    store.recordMatch("run1", 1, 1, 0.72, 43);
    const matches = store.getMatches("run1", 1);
    expect(matches).toHaveLength(2);
    expect(matches[0].score).toBe(0.85);
  });

  it("appends and retrieves agent outputs", () => {
    store.createRun("run1", "grid_ctf", 1, "generation", "anthropic");
    store.appendAgentOutput("run1", 1, "competitor", "strategy json here");
    store.appendAgentOutput("run1", 1, "analyst", "analysis markdown");
    const outputs = store.getAgentOutputs("run1", 1);
    expect(outputs).toHaveLength(2);
    expect(outputs[0].role).toBe("competitor");
  });

  it("builds score trajectory across generations", () => {
    store.createRun("run1", "grid_ctf", 3, "generation", "anthropic");
    store.upsertGeneration("run1", 1, { meanScore: 0.5, bestScore: 0.6, elo: 1000, wins: 1, losses: 2, gateDecision: "advance", status: "completed" });
    store.upsertGeneration("run1", 2, { meanScore: 0.7, bestScore: 0.8, elo: 1050, wins: 2, losses: 1, gateDecision: "advance", status: "completed" });
    const trajectory = store.getScoreTrajectory("run1");
    expect(trajectory).toHaveLength(2);
    expect(trajectory[1].bestScore).toBe(0.8);
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd ts && npx vitest run tests/storage-runs.test.ts`
Expected: FAIL — methods don't exist

- [ ] **Step 4: Implement storage methods**

Add to `ts/src/storage/index.ts`: `createRun()`, `getRun()`, `completeRun()`, `upsertGeneration()`, `getGeneration()`, `recordMatch()`, `getMatches()`, `appendAgentOutput()`, `getAgentOutputs()`, `getScoreTrajectory()`, `listRuns()`.

Each method is a straightforward prepared statement against the tables created in migration 008.

- [ ] **Step 5: Run tests**

Run: `cd ts && npx vitest run tests/storage-runs.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ts/migrations/009-runs-generations.sql ts/src/storage/index.ts ts/tests/storage-runs.test.ts
git commit -m "feat(ts): add run/generation/match/agent_output storage tables"
```

---

### Task 3: Event Emitter

**Files:**
- Create: `ts/src/loop/events.ts`
- Test: `ts/tests/events.test.ts`
- Modify: `ts/src/loop/index.ts`

**Python reference:** `autocontext/src/autocontext/loop/events.py`

NDJSON event stream written to disk + in-memory subscriber dispatch.

- [ ] **Step 1: Write failing test**

```typescript
// ts/tests/events.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { EventStreamEmitter } from "../src/loop/events.js";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("EventStreamEmitter", () => {
  let dir: string;
  let emitter: EventStreamEmitter;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "autoctx-events-"));
    emitter = new EventStreamEmitter(join(dir, "events.ndjson"));
  });

  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  it("writes events to NDJSON file", () => {
    emitter.emit("generation_started", { runId: "r1", generation: 1 });
    emitter.flush();
    const lines = readFileSync(join(dir, "events.ndjson"), "utf-8").trim().split("\n");
    expect(lines).toHaveLength(1);
    const event = JSON.parse(lines[0]);
    expect(event.event).toBe("generation_started");
    expect(event.payload.runId).toBe("r1");
    expect(event.timestamp).toBeDefined();
  });

  it("notifies subscribers synchronously", () => {
    const received: string[] = [];
    emitter.subscribe((event, payload) => received.push(event));
    emitter.emit("match_completed", { score: 0.85 });
    expect(received).toEqual(["match_completed"]);
  });
});
```

- [ ] **Step 2: Run test → FAIL**
- [ ] **Step 3: Implement EventStreamEmitter**

```typescript
// ts/src/loop/events.ts
import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

export type EventSubscriber = (event: string, payload: Record<string, unknown>) => void;

export class EventStreamEmitter {
  private path: string;
  private buffer: string[] = [];
  private subscribers: EventSubscriber[] = [];

  constructor(path: string) {
    this.path = path;
    mkdirSync(dirname(path), { recursive: true });
  }

  emit(event: string, payload: Record<string, unknown>): void {
    const entry = JSON.stringify({
      event,
      payload,
      timestamp: new Date().toISOString(),
    });
    this.buffer.push(entry);
    for (const sub of this.subscribers) {
      sub(event, payload);
    }
  }

  flush(): void {
    if (this.buffer.length === 0) return;
    appendFileSync(this.path, this.buffer.join("\n") + "\n");
    this.buffer = [];
  }

  subscribe(fn: EventSubscriber): () => void {
    this.subscribers.push(fn);
    return () => {
      this.subscribers = this.subscribers.filter((s) => s !== fn);
    };
  }
}
```

- [ ] **Step 4: Run test → PASS**
- [ ] **Step 5: Update `ts/src/loop/index.ts` to re-export**
- [ ] **Step 6: Commit**

```bash
git add ts/src/loop/events.ts ts/src/loop/index.ts ts/tests/events.test.ts
git commit -m "feat(ts): add NDJSON event emitter for generation loop"
```

---

### Task 4: Controller (Pause/Resume)

**Files:**
- Create: `ts/src/loop/controller.ts`
- Test: `ts/tests/controller.test.ts`

**Python reference:** `autocontext/src/autocontext/loop/controller.py`

Simple state machine: running ↔ paused. The generation runner checks `controller.shouldPause()` at stage boundaries.

- [ ] **Step 1: Write failing test**

```typescript
describe("LoopController", () => {
  it("starts in running state", () => {
    const ctrl = new LoopController();
    expect(ctrl.isPaused).toBe(false);
  });

  it("pauses and resumes", async () => {
    const ctrl = new LoopController();
    ctrl.pause();
    expect(ctrl.isPaused).toBe(true);
    ctrl.resume();
    expect(ctrl.isPaused).toBe(false);
  });

  it("waitIfPaused resolves immediately when not paused", async () => {
    const ctrl = new LoopController();
    await ctrl.waitIfPaused(); // should not hang
  });

  it("waitIfPaused blocks until resumed", async () => {
    const ctrl = new LoopController();
    ctrl.pause();
    let resolved = false;
    const p = ctrl.waitIfPaused().then(() => { resolved = true; });
    expect(resolved).toBe(false);
    ctrl.resume();
    await p;
    expect(resolved).toBe(true);
  });
});
```

- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement LoopController with Promise-based wait**
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit**

---

## Phase 2: Scenarios & Execution (Tasks 5–9)

### Task 5: ScenarioInterface (Game Interface)

**Files:**
- Create: `ts/src/scenarios/game-interface.ts`
- Test: `ts/tests/game-interface.test.ts`

**Python reference:** `autocontext/src/autocontext/scenarios/__init__.py` (ScenarioInterface ABC)

Define the full multi-step state machine interface that game scenarios implement. This must match the Python ABC — it is NOT just a single `executeMatch()` call. The orchestrator, prompt templates, and TUI depend on individual methods.

- [ ] **Step 1: Define interface with Zod schemas**

```typescript
// ts/src/scenarios/game-interface.ts
import { z } from "zod";

export const ExecutionLimitsSchema = z.object({
  timeoutSeconds: z.number().default(30),
  maxMemoryMb: z.number().default(256),
});
export type ExecutionLimits = z.infer<typeof ExecutionLimitsSchema>;

export const MatchResultSchema = z.object({
  score: z.number().min(0).max(1),
  win: z.boolean(),
  details: z.record(z.unknown()).default({}),
});
export type MatchResult = z.infer<typeof MatchResultSchema>;

export const ReplayEnvelopeSchema = z.object({
  scenario: z.string(),
  seed: z.number(),
  moves: z.array(z.record(z.unknown())).default([]),
  finalState: z.record(z.unknown()).default({}),
});
export type ReplayEnvelope = z.infer<typeof ReplayEnvelopeSchema>;

export interface ScoringComponent {
  name: string;
  description: string;
  weight: number;
}

/**
 * Interface for tournament-based game scenarios.
 *
 * Mirrors the Python ScenarioInterface ABC. The generation runner calls
 * executeMatch() which internally runs the full state machine loop
 * (initialState → step → isTerminal → getResult). Individual methods
 * are also used by the orchestrator for prompt injection.
 */
export interface ScenarioInterface {
  /** Human-readable rules description for agent prompts. */
  describeRules(): string;
  /** Current observation for agent prompts (state-aware narrative). */
  getObservation(state: Record<string, unknown>): string;
  /** Strategy parameter interface description. */
  getStrategyInterface(): string;
  /** Evaluation criteria description. */
  describeEvaluationCriteria(): string;
  /** Scoring dimensions with weights. */
  scoringDimensions(): ScoringComponent[];

  /** Create initial game state from a seed. */
  initialState(seed: number): Record<string, unknown>;
  /** Apply one step of the strategy to the state. Returns new state. */
  step(state: Record<string, unknown>, strategy: Record<string, unknown>): Record<string, unknown>;
  /** Check if the game has reached a terminal state. */
  isTerminal(state: Record<string, unknown>): boolean;
  /** Compute final result from terminal state. */
  getResult(state: Record<string, unknown>): MatchResult;

  /** Execute a full match (init → step loop → result). Convenience wrapper. */
  executeMatch(
    strategy: Record<string, unknown>,
    seed: number,
    limits?: ExecutionLimits,
  ): { result: MatchResult; replay: ReplayEnvelope };

  /** Optional: enumerate legal actions in current state (for filter harness). */
  enumerateLegalActions?(state: Record<string, unknown>): Array<Record<string, unknown>>;
  /** Optional: validate proposed actions (for verify harness). */
  validateActions?(state: Record<string, unknown>, actions: Record<string, unknown>[]): string[];
  /** Optional: convert replay to human-readable narrative. */
  replayToNarrative?(replay: ReplayEnvelope): string;
  /** Optional: provide seed tools for architect. */
  seedTools?(): string[];
}
```

- [ ] **Step 2: Write test confirming interface shape** — create a minimal mock scenario implementing all required methods and verify executeMatch runs the step loop correctly.
- [ ] **Step 3: Commit**

---

### Task 6: Grid CTF Scenario

**Files:**
- Create: `ts/src/scenarios/grid-ctf.ts`
- Test: `ts/tests/grid-ctf.test.ts`

**Python reference:** `autocontext/src/autocontext/scenarios/grid_ctf.py`

Port the Grid CTF game logic — a grid-based capture-the-flag with parameterized strategies.

- [ ] **Step 1: Write failing tests for Grid CTF**

Test: deterministic match execution with known seed produces expected score. Test: invalid strategy parameters clamp to valid range. Test: different seeds produce different outcomes.

- [ ] **Step 2: Implement GridCtfScenario**

Port the Python grid logic: grid generation from seed, agent movement simulation, flag capture scoring. Must implement `ScenarioInterface`.

Port the actual Python strategy parameters (check `autocontext/src/autocontext/scenarios/grid_ctf.py` for current params — known to include `aggression`, `defense`, `path_bias` but may have evolved). Do NOT guess parameters — read the Python source.

- [ ] **Step 3: Run tests → PASS**
- [ ] **Step 4: Commit**

---

### Task 7: Scenario Registry

**Files:**
- Create: `ts/src/scenarios/registry.ts`
- Test: `ts/tests/scenario-registry.test.ts`
- Modify: `ts/src/scenarios/index.ts`

**Python reference:** `autocontext/src/autocontext/scenarios/__init__.py`

Dual-interface registry: game scenarios + agent task scenarios in one map.

- [ ] **Step 1: Write failing test**

```typescript
describe("ScenarioRegistry", () => {
  it("registers and retrieves grid_ctf", () => {
    const entry = SCENARIO_REGISTRY.get("grid_ctf");
    expect(entry).toBeDefined();
  });

  it("detects game scenario family", () => {
    const entry = SCENARIO_REGISTRY.get("grid_ctf")!;
    const instance = new entry();
    expect(isGameScenario(instance)).toBe(true);
    expect(isAgentTask(instance)).toBe(false);
  });
});
```

- [ ] **Step 2: Implement registry with `isGameScenario` / `isAgentTask` guards**
- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 8: Elo Scoring

**Files:**
- Create: `ts/src/execution/elo.ts`
- Test: `ts/tests/elo.test.ts`

**Python reference:** `autocontext/src/autocontext/execution/elo.py`

- [ ] **Step 1: Write failing test**

```typescript
describe("Elo", () => {
  it("expectedScore returns 0.5 for equal ratings", () => {
    expect(expectedScore(1000, 1000)).toBeCloseTo(0.5);
  });

  it("updateElo increases winner rating", () => {
    const [a, b] = updateElo(1000, 1000, 1.0); // a wins
    expect(a).toBeGreaterThan(1000);
    expect(b).toBeLessThan(1000);
  });
});
```

- [ ] **Step 2: Implement Elo functions**

```typescript
export function expectedScore(eloA: number, eloB: number): number {
  return 1.0 / (1.0 + Math.pow(10, (eloB - eloA) / 400));
}

export function updateElo(
  eloA: number,
  eloB: number,
  scoreA: number,
  k: number = 24,  // matches Python k_factor=24.0
): [number, number] {
  const ea = expectedScore(eloA, eloB);
  const newA = eloA + k * (scoreA - ea);
  const newB = eloB + k * ((1 - scoreA) - (1 - ea));
  return [newA, newB];
}
```

- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 8b: Execution Supervisor

**Files:**
- Create: `ts/src/execution/supervisor.ts`
- Test: `ts/tests/supervisor.test.ts`

**Python reference:** `autocontext/src/autocontext/execution/supervisor.py`

Abstraction layer between tournament and scenario. Handles timeout enforcement, memory limits, and executor dispatch (local now, secure-exec sandbox later). Without this, Task 33 (secure-exec) would require retrofitting the tournament.

- [ ] **Step 1: Write failing test**

```typescript
it("wraps scenario.executeMatch with timeout enforcement", () => {
  const supervisor = new ExecutionSupervisor({ timeoutSeconds: 5 });
  const result = supervisor.execute(mockScenario, strategy, seed);
  expect(result.result.score).toBeDefined();
});
```

- [ ] **Step 2: Implement ExecutionSupervisor**

```typescript
export class ExecutionSupervisor {
  constructor(private opts: { timeoutSeconds: number; executor?: "local" | "sandbox" }) {}
  execute(scenario: ScenarioInterface, strategy: Record<string, unknown>, seed: number): { result: MatchResult; replay: ReplayEnvelope } { ... }
}
```

- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 9: Tournament Runner

**Files:**
- Create: `ts/src/execution/tournament.ts`
- Test: `ts/tests/tournament.test.ts`

**Python reference:** `autocontext/src/autocontext/loop/generation_runner.py` (match loop section)

Runs N matches with a strategy against a scenario, aggregates scores, computes Elo.

- [ ] **Step 1: Write failing test**

```typescript
describe("Tournament", () => {
  it("runs matches and aggregates scores", () => {
    const mockScenario: ScenarioInterface = { /* mock with deterministic scores */ };
    const result = runTournament(mockScenario, { aggression: 0.5 }, {
      matchCount: 3,
      seedBase: 1000,
      currentElo: 1000,
    });
    expect(result.matches).toHaveLength(3);
    expect(result.meanScore).toBeGreaterThanOrEqual(0);
    expect(result.meanScore).toBeLessThanOrEqual(1);
    expect(typeof result.elo).toBe("number");
  });
});
```

- [ ] **Step 2: Implement runTournament**

```typescript
export interface TournamentResult {
  matches: Array<{ index: number; score: number; seed: number }>;
  meanScore: number;
  bestScore: number;
  wins: number;
  losses: number;
  elo: number;
}

export function runTournament(
  scenario: ScenarioInterface,
  strategy: Record<string, unknown>,
  opts: { matchCount: number; seedBase: number; currentElo: number },
): TournamentResult { ... }
```

- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

## Phase 3: Knowledge System (Tasks 10–12)

### Task 10: Playbook Manager

**Files:**
- Create: `ts/src/knowledge/playbook.ts`
- Test: `ts/tests/playbook.test.ts`

**Python reference:** `autocontext/src/autocontext/knowledge/` (playbook read/write/version/rollback)

Versioned markdown playbook with sections delimited by `<!-- PLAYBOOK_START/END -->`, `<!-- LESSONS_START/END -->`, `<!-- COMPETITOR_HINTS_START/END -->`.

- [ ] **Step 1: Write failing tests**

Test: write playbook → read back identical content. Test: write 3 versions → only `maxVersions` retained. Test: rollback restores previous version. Test: parse sections from markdown.

- [ ] **Step 2: Implement PlaybookManager**

```typescript
export class PlaybookManager {
  constructor(private knowledgeDir: string, private maxVersions: number = 5) {}
  read(scenario: string): string | null { ... }
  write(scenario: string, content: string): void { ... }
  rollback(scenario: string): boolean { ... }
  parseSections(content: string): { playbook: string; lessons: string; hints: string } { ... }
}
```

- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 10b: Artifact Store

**Files:**
- Create: `ts/src/knowledge/artifact-store.ts`
- Test: `ts/tests/artifact-store.test.ts`

**Python reference:** `autocontext/src/autocontext/storage/artifact_store.py` (977 lines)

The PlaybookManager (Task 10) handles playbook read/write/rollback. The ArtifactStore handles everything else the generation runner persists to disk: tools (architect-generated), snapshots (cross-run), lessons, hints, buffered writes, and skill symlinks. The Python `GenerationRunner` depends heavily on this.

- [ ] **Step 1: Write failing tests**

Test: write + read tool file. Test: archive old tool version. Test: save/load snapshot directory. Test: read/write hints. Test: read/write lessons with consolidation.

- [ ] **Step 2: Implement ArtifactStore**

```typescript
export class ArtifactStore {
  constructor(private knowledgeRoot: string) {}

  // Tools
  writeToolSource(scenario: string, name: string, source: string): void { ... }
  readToolSource(scenario: string, name: string): string | null { ... }
  listTools(scenario: string): string[] { ... }
  archiveTool(scenario: string, name: string): void { ... }

  // Snapshots (cross-run inheritance)
  saveSnapshot(scenario: string, runId: string): void { ... }
  loadLatestSnapshot(scenario: string): string | null { ... }

  // Hints
  readHints(scenario: string): string { ... }
  writeHints(scenario: string, content: string): void { ... }

  // Lessons
  readLessons(scenario: string): string[] { ... }
  writeLessons(scenario: string, lessons: string[]): void { ... }
  consolidateLessons(scenario: string, maxLessons: number): void { ... }

  // Analysis artifacts
  writeAnalysis(scenario: string, generation: number, content: string): void { ... }
}
```

- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 11: Score Trajectory Builder

**Files:**
- Create: `ts/src/knowledge/trajectory.ts`
- Test: `ts/tests/trajectory.test.ts`

**Python reference:** `autocontext/src/autocontext/knowledge/trajectory.py`

Builds a markdown table of generation scores for injection into agent prompts.

- [ ] **Step 1: Write failing test**

```typescript
it("builds markdown trajectory table", () => {
  const data = [
    { generation: 1, meanScore: 0.50, bestScore: 0.65, elo: 1000, gateDecision: "advance" },
    { generation: 2, meanScore: 0.72, bestScore: 0.85, elo: 1050, gateDecision: "advance" },
  ];
  const md = buildTrajectory(data);
  expect(md).toContain("| Gen | Mean | Best | Elo | Gate | Delta |");
  expect(md).toContain("| 2 ");
  expect(md).toContain("+0.20"); // delta from 0.65 to 0.85
});
```

- [ ] **Step 2: Implement buildTrajectory**
- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 12: Context Budget Manager

**Files:**
- Create: `ts/src/prompts/context-budget.ts`
- Test: `ts/tests/context-budget.test.ts`

**Python reference:** `autocontext/src/autocontext/prompts/context_budget.py`

Allocates token budget across prompt sections with priority-based truncation.

- [ ] **Step 1: Write failing test**

Test: total output stays under budget. Test: low-priority sections get truncated first. Test: high-priority sections (playbook, trajectory) are preserved.

- [ ] **Step 2: Implement ContextBudget**

```typescript
export class ContextBudget {
  constructor(private maxTokens: number) {}
  allocate(sections: Array<{ key: string; content: string; priority: number }>): Map<string, string> { ... }
}
```

Simple word-count estimator (tokens ≈ words × 1.3). Sorts sections by priority, truncates lowest-priority sections first.

- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

## Phase 4: Agent Orchestration (Tasks 13–18)

### Task 13: Role Definitions & Output Parsing

**Files:**
- Create: `ts/src/agents/roles.ts`
- Test: `ts/tests/agent-roles.test.ts`

**Python reference:** `autocontext/src/autocontext/agents/` (role-specific parsing), `autocontext/src/autocontext/prompts/templates.py` (role prompts)

Define the 6 agent roles, their output markers, and parsers.

- [ ] **Step 1: Write failing tests**

Test: parse competitor JSON strategy from raw LLM output. Test: parse analyst sections (Findings, Root Causes, Recommendations). Test: parse coach playbook sections from delimiters. Test: parse architect tool specs from `<!-- HARNESS_START/END -->` markers.

- [ ] **Step 2: Implement role definitions**

```typescript
export type AgentRole = "competitor" | "translator" | "analyst" | "coach" | "architect" | "curator";

export interface RoleOutput {
  role: AgentRole;
  raw: string;
  parsed: Record<string, unknown>;
  usage?: { inputTokens: number; outputTokens: number; latencyMs: number; model: string };
}

export function parseCompetitorOutput(raw: string, codeStrategies: boolean): Record<string, unknown> { ... }
export function parseAnalystOutput(raw: string): { findings: string; rootCauses: string; recommendations: string } { ... }
export function parseCoachOutput(raw: string): { playbook: string; lessons: string; hints: string } { ... }
export function parseArchitectOutput(raw: string): { tools: string[]; harnessSpecs: string[] } { ... }
export function parseCuratorOutput(raw: string): { decision: "accept" | "reject" | "merge"; reasoning: string } { ... }
```

- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 14: Prompt Template Assembly

**Files:**
- Create: `ts/src/prompts/templates.ts`
- Create: `ts/src/prompts/index.ts`
- Test: `ts/tests/prompt-templates.test.ts`

**Python reference:** `autocontext/src/autocontext/prompts/templates.py`

Assembles the `PromptBundle` — one prompt per role, enriched with scenario context, trajectory, playbook, tools, constraints.

- [ ] **Step 1: Write failing tests**

Test: bundle includes scenario rules in competitor prompt. Test: trajectory table injected when generations > 0. Test: constraint suffixes appended when enabled. Test: context budget applied.

- [ ] **Step 2: Implement buildPromptBundle**

```typescript
export interface PromptBundle {
  competitor: string;
  translator: string;
  analyst: string;
  coach: string;
  architect: string;
}

export interface PromptContext {
  scenarioRules: string;
  strategyInterface: string;
  observation: string;
  playbook: string;
  lessons: string;
  hints: string;
  trajectory: string;
  tools: string[];
  generation: number;
  constraintsEnabled: boolean;
  codeStrategiesEnabled: boolean;
  contextBudgetTokens: number;
}

export function buildPromptBundle(ctx: PromptContext): PromptBundle { ... }
```

- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 15: Provider Bridge

**Files:**
- Create: `ts/src/agents/provider-bridge.ts`
- Test: `ts/tests/provider-bridge.test.ts`

**Python reference:** `autocontext/src/autocontext/agents/provider_bridge.py`

Adapts `AgentRuntime` (ClaudeCLI, CodexCLI) into the `LLMProvider` interface so the orchestrator can treat all backends uniformly.

- [ ] **Step 1: Write failing test**

```typescript
it("RuntimeBridge wraps ClaudeCLIRuntime as LLMProvider", () => {
  const mockRuntime = { generate: () => ({ text: "hello", structured: null }), name: "claude-cli" };
  const bridge = new RuntimeBridge(mockRuntime);
  const result = await bridge.complete({ model: "sonnet", systemPrompt: "", userPrompt: "test", maxTokens: 100, temperature: 0 });
  expect(result.text).toBe("hello");
});
```

- [ ] **Step 2: Implement RuntimeBridge**
- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 16: Model Router

**Files:**
- Create: `ts/src/agents/model-router.ts`
- Test: `ts/tests/model-router.test.ts`

**Python reference:** `autocontext/src/autocontext/agents/model_router.py`

Tier-based model selection: Haiku → Sonnet → Opus based on generation number, retry count, and plateau detection.

- [ ] **Step 1: Write failing tests**

Test: competitor gets Haiku for first N gens. Test: competitor escalates to Sonnet after N gens. Test: coach always starts at Sonnet minimum. Test: plateau triggers Opus escalation.

- [ ] **Step 2: Implement ModelRouter**
- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 17: Codex CLI Runtime

**Files:**
- Create: `ts/src/runtimes/codex-cli.ts`
- Test: `ts/tests/codex-cli.test.ts`

**Python reference:** `autocontext/src/autocontext/runtimes/codex_cli.py`

Wraps `codex exec` subprocess, parses JSONL event stream output.

- [ ] **Step 1: Write failing tests for output parsing**

Test: parse JSONL event stream. Test: parse plain text fallback. Test: handle timeout. Test: handle missing binary.

- [ ] **Step 2: Implement CodexCLIRuntime** (mirrors ClaudeCLIRuntime pattern)
- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 18: Agent Orchestrator

**Files:**
- Create: `ts/src/agents/orchestrator.ts`
- Create: `ts/src/agents/index.ts`
- Test: `ts/tests/orchestrator.test.ts`

**Python reference:** `autocontext/src/autocontext/agents/orchestrator.py`

The core agent dispatch: runs Competitor → Translator → Analyst/Coach/Architect (parallel) → optional Curator.

- [ ] **Step 1: Write failing tests**

Test with mock LLM provider: runs all roles in order. Test: analyst/coach/architect run in parallel (Promise.all). Test: per-role provider overrides are applied. Test: architect only runs every N generations.

- [ ] **Step 2: Implement AgentOrchestrator**

```typescript
export interface AgentOutputs {
  strategy: Record<string, unknown>;
  analysis: string;
  playbook: string;
  lessons: string;
  hints: string;
  tools: string[];
  curatorDecision?: "accept" | "reject" | "merge";
  roleMetrics: Map<AgentRole, { inputTokens: number; outputTokens: number; latencyMs: number; model: string }>;
}

export class AgentOrchestrator {
  constructor(
    private defaultProvider: LLMProvider,
    private settings: AppSettings,
  ) {}

  async runGeneration(
    prompts: PromptBundle,
    generation: number,
    opts?: { roleOverrides?: Map<AgentRole, LLMProvider> },
  ): Promise<AgentOutputs> { ... }
}
```

Key implementation detail: use `Promise.all([analystCall, coachCall, architectCall])` for parallel execution (natural in TS vs Python's ThreadPoolExecutor).

- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

## Phase 5: Generation Loop (Tasks 19–23)

### Task 19: Deterministic Provider (Testing)

**Files:**
- Create: `ts/src/providers/deterministic.ts`
- Modify: `ts/src/providers/index.ts`
- Test: `ts/tests/deterministic-provider.test.ts`

A canned-response provider for CI and offline testing (mirrors Python `DeterministicDevClient`). **Must be built before the GenerationRunner** since all generation loop tests depend on it.

- [ ] **Step 1: Write failing test**

```typescript
it("returns canned competitor strategy", async () => {
  const provider = createDeterministicProvider();
  const result = await provider.complete({ model: "test", systemPrompt: "", userPrompt: "competitor prompt", maxTokens: 100, temperature: 0 });
  const parsed = JSON.parse(result.text);
  expect(parsed.aggression).toBeDefined();
});
```

- [ ] **Step 2: Implement DeterministicProvider**

Returns fixed JSON strategy for competitor, canned analysis for analyst, playbook template for coach, etc. Role detection via prompt keyword matching.

- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 20: Backpressure Gate

**Files:**
- Create: `ts/src/loop/backpressure.ts`
- Test: `ts/tests/backpressure.test.ts`

**Python reference:** `autocontext/src/autocontext/loop/generation_runner.py` (gate logic)

- [ ] **Step 1: Write failing tests**

```typescript
describe("BackpressureGate", () => {
  it("advances when delta exceeds threshold", () => {
    const gate = new BackpressureGate({ minDelta: 0.005, mode: "simple" });
    const result = gate.evaluate({ currentBest: 0.60, previousBest: 0.50, retryCount: 0, maxRetries: 2 });
    expect(result.decision).toBe("advance");
    expect(result.delta).toBeCloseTo(0.10);
  });

  it("retries when delta is negative and retries remain", () => {
    const gate = new BackpressureGate({ minDelta: 0.005, mode: "simple" });
    const result = gate.evaluate({ currentBest: 0.45, previousBest: 0.50, retryCount: 0, maxRetries: 2 });
    expect(result.decision).toBe("retry");
  });

  it("rollbacks when retries exhausted", () => {
    const gate = new BackpressureGate({ minDelta: 0.005, mode: "simple" });
    const result = gate.evaluate({ currentBest: 0.45, previousBest: 0.50, retryCount: 2, maxRetries: 2 });
    expect(result.decision).toBe("rollback");
  });

  it("retries when delta is tiny but positive", () => {
    const gate = new BackpressureGate({ minDelta: 0.005, mode: "simple" });
    const result = gate.evaluate({ currentBest: 0.502, previousBest: 0.50, retryCount: 0, maxRetries: 2 });
    expect(result.decision).toBe("retry");
  });
});
```

Also implement `TrendAwareGate` with plateau detection and relaxation factor (Python `backpressure_mode="trend"`).

- [ ] **Step 2: Implement BackpressureGate + TrendAwareGate**

```typescript
export type GateDecision = "advance" | "retry" | "rollback";

export interface GateResult {
  decision: GateDecision;
  delta: number;
  threshold: number;
  reason: string;
}

export interface GateInput {
  currentBest: number;
  previousBest: number;
  retryCount: number;
  maxRetries: number;
}

export class BackpressureGate {
  constructor(private opts: { minDelta: number; mode: "simple" | "trend"; plateauWindow?: number; plateauRelaxation?: number }) {}
  evaluate(input: GateInput): GateResult { ... }
}
```

- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 21: Generation Runner

**Files:**
- Create: `ts/src/loop/generation-runner.ts`
- Test: `ts/tests/generation-runner.test.ts`
- Modify: `ts/src/loop/index.ts`

**Python reference:** `autocontext/src/autocontext/loop/generation_runner.py` (1144 lines)

This is the heart of the system. It orchestrates the full per-generation cycle.

- [ ] **Step 1: Write integration test with mock provider**

```typescript
describe("GenerationRunner", () => {
  it("runs 1 generation end-to-end with deterministic provider", async () => {
    const settings = loadSettings(); // defaults
    settings.agentProvider = "deterministic";
    settings.matchesPerGeneration = 1;

    const runner = new GenerationRunner(settings);
    const summary = await runner.run({
      scenarioName: "grid_ctf",
      generations: 1,
      runId: "test_run",
    });

    expect(summary.generationsExecuted).toBe(1);
    expect(summary.bestScore).toBeGreaterThanOrEqual(0);
  });
});
```

- [ ] **Step 2: Implement GenerationRunner**

```typescript
export interface RunSummary {
  runId: string;
  scenario: string;
  generationsExecuted: number;
  bestScore: number;
  currentElo: number;
}

export class GenerationRunner {
  controller: LoopController;
  events: EventStreamEmitter;

  constructor(private settings: AppSettings) { ... }

  async run(opts: {
    scenarioName: string;
    generations: number;
    runId: string;
  }): Promise<RunSummary> { ... }

  /** Single generation cycle */
  private async runGeneration(
    scenario: ScenarioInterface,
    generation: number,
    runId: string,
    previousBest: number,
  ): Promise<{ bestScore: number; elo: number; gateDecision: GateDecision }> {
    // 1. Load knowledge (playbook, trajectory, tools)
    // 2. Build prompt bundle
    // 3. Orchestrate agents
    // 4. Parse strategy from competitor output
    // 5. Run tournament
    // 6. Backpressure gate
    // 7. Persist results (on advance)
    // 8. Emit events
  }
}
```

The algorithm per generation (mirrors Python exactly):

1. `controller.waitIfPaused()`
2. Load scenario + playbook + trajectory from store
3. `buildPromptBundle()` with context
4. `orchestrator.runGeneration(prompts, gen)`
5. Parse `agentOutputs.strategy`
6. `runTournament(scenario, strategy, { matchCount, seedBase, currentElo })`
7. `backpressureGate.evaluate(tournamentResult.bestScore, previousBest)`
8. If `advance`: write playbook, persist to SQLite, emit `generation_completed`
9. If `retry`: re-run (up to maxRetries), then rollback
10. If `rollback`: revert playbook, emit event

- [ ] **Step 3: Implement deterministic provider for testing**

A simple mock provider that returns canned responses per role, matching the Python `DeterministicDevClient`.

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit**

---

### Task 22: CLI `run` Command

**Files:**
- Modify: `ts/src/cli/index.ts`
- Test: `ts/tests/cli-run.test.ts`

Add the `run` command to the CLI:

```
autoctx run --scenario grid_ctf --gens 3 [--run-id my_run] [--preset quick] [--json]
```

- [ ] **Step 1: Write failing test**

```typescript
it("run command accepts --scenario and --gens", async () => {
  // Set deterministic provider
  process.env.AUTOCONTEXT_AGENT_PROVIDER = "deterministic";
  // Invoke CLI programmatically or test arg parsing
});
```

- [ ] **Step 2: Add `run` case to CLI switch**

```typescript
case "run":
  await cmdRun(dbPath);
  break;
```

```typescript
async function cmdRun(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      scenario: { type: "string", default: "grid_ctf" },
      gens: { type: "string", default: "1" },
      "run-id": { type: "string" },
      preset: { type: "string" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log("autoctx run --scenario <name> --gens <N> [--run-id <id>] [--preset <name>] [--json]");
    process.exit(0);
  }

  if (values.preset) process.env.AUTOCONTEXT_PRESET = values.preset;

  const { loadSettings } = await import("../config/settings.js");
  const { GenerationRunner } = await import("../loop/generation-runner.js");

  const settings = loadSettings();
  const runner = new GenerationRunner(settings);

  const summary = await runner.run({
    scenarioName: values.scenario!,
    generations: parseInt(values.gens ?? "1", 10),
    runId: values["run-id"] ?? `run_${Date.now().toString(36)}`,
  });

  if (values.json) {
    console.log(JSON.stringify(summary, null, 2));
  } else {
    console.log(`Run ${summary.runId} completed: ${summary.generationsExecuted} generations, best score ${summary.bestScore.toFixed(4)}`);
  }
}
```

- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Update CLI help text**
- [ ] **Step 5: Commit**

```bash
git add ts/src/cli/index.ts ts/tests/cli-run.test.ts
git commit -m "feat(ts): add autoctx run command for generation loop"
```

---

### Task 23: End-to-End Integration Smoke Test

**Files:**
- Create: `ts/tests/smoke-run.test.ts`

A CLI-level integration test mirroring the Python CI smoke tests.

- [ ] **Step 1: Write integration test**

```typescript
import { describe, it, expect } from "vitest";
import { execFileSync } from "node:child_process";

describe("smoke: autoctx run", () => {
  it("runs grid_ctf for 1 gen with deterministic provider", () => {
    const result = execFileSync("npx", [
      "tsx", "src/cli/index.ts", "run",
      "--scenario", "grid_ctf", "--gens", "1", "--json",
    ], {
      env: { ...process.env, AUTOCONTEXT_AGENT_PROVIDER: "deterministic" },
      cwd: resolve(__dirname, ".."),
      encoding: "utf-8",
      timeout: 30_000,
    });
    const summary = JSON.parse(result);
    expect(summary.generationsExecuted).toBe(1);
    expect(summary.bestScore).toBeGreaterThanOrEqual(0);
  });
});
```

- [ ] **Step 2: Run → PASS** (depends on all Phase 1-5 tasks being complete)
- [ ] **Step 3: Commit**

---

## Phase 6: Interactive Server & TUI (Tasks 24–28)

### Task 24: WebSocket Protocol Types

**Files:**
- Create: `ts/src/server/protocol.ts`
- Test: `ts/tests/server-protocol.test.ts`

**Python reference:** `autocontext/src/autocontext/server/protocol.py`

Zod schemas for all client↔server messages.

- [ ] **Step 1: Define message schemas** (mirrors Python protocol exactly)

Server→Client: `hello`, `event`, `state`, `chat_response`, `environments`, `run_accepted`, `ack`, `error`, `scenario_generating`, `scenario_preview`, `scenario_ready`, `scenario_error`

Client→Server: `pause`, `resume`, `inject_hint`, `override_gate`, `chat_agent`, `start_run`, `list_scenarios`, `create_scenario`, `confirm_scenario`, `revise_scenario`, `cancel_scenario`

- [ ] **Step 2: Write parse/validate tests**
- [ ] **Step 3: Commit**

---

### Task 25: WebSocket Server

**Files:**
- Create: `ts/src/server/ws-server.ts`
- Test: `ts/tests/ws-server.test.ts`

Replace FastAPI+uvicorn with a lightweight `ws` WebSocket server.

- [ ] **Step 1: Write failing test**

```typescript
it("accepts WS connection and sends hello", async () => {
  const server = createWsServer({ port: 0 });
  const addr = server.address();
  const ws = new WebSocket(`ws://localhost:${addr.port}/ws/interactive`);
  const msg = await new Promise((resolve) => ws.onmessage = (e) => resolve(JSON.parse(e.data)));
  expect(msg.type).toBe("hello");
  ws.close();
  server.close();
});
```

- [ ] **Step 2: Implement WS server with message routing**

Handle: connection → send `hello` + `environments`. Route incoming `ClientMessage` to handlers (pause, resume, start_run, create_scenario, etc.).

- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 26: Run Manager

**Files:**
- Create: `ts/src/server/run-manager.ts`
- Test: `ts/tests/run-manager.test.ts`

**Python reference:** `autocontext/src/autocontext/server/run_manager.py`

Manages run lifecycle for the interactive server — starts runs in background, tracks active state.

- [ ] **Step 1: Write failing test**
- [ ] **Step 2: Implement RunManager** (starts GenerationRunner in a worker or async task)
- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 27: Bundle TUI into Package

**Files:**
- Move: `tui/src/*` → `ts/src/tui/`
- Modify: `ts/package.json` (add ink, react, ws deps)
- Modify: `ts/tsconfig.json` (add JSX support)

The Ink TUI is already TypeScript/React. Moving it into the `autoctx` package makes it a built-in command.

- [ ] **Step 1: Move TUI source files**

```bash
cp -r tui/src/* ts/src/tui/
```

- [ ] **Step 2: Add dependencies to ts/package.json**

```json
"dependencies": {
  "ink": "^5.1.0",
  "ink-text-input": "^6.0.0",
  "react": "^18.3.1",
  "ws": "^8.18.0",
  // ... existing deps
}
```

- [ ] **Step 3: Update tsconfig.json for JSX**

```json
"compilerOptions": {
  "jsx": "react-jsx",
  // ... existing options
}
```

- [ ] **Step 4: Verify TUI builds**

Run: `cd ts && npm run build`
Expected: No type errors

- [ ] **Step 5: Commit**

---

### Task 28: CLI `tui` Command

**Files:**
- Modify: `ts/src/cli/index.ts`

Add the `tui` command that starts the WS server + renders the Ink app:

```
autoctx tui [--port 8000]
```

- [ ] **Step 1: Add tui case to CLI**

```typescript
case "tui":
  await cmdTui();
  break;
```

```typescript
async function cmdTui(): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      port: { type: "string", default: "8000" },
    },
  });

  const port = parseInt(values.port!, 10);

  // Start WS server
  const { createWsServer } = await import("../server/ws-server.js");
  const { RunManager } = await import("../server/run-manager.js");
  const { loadSettings } = await import("../config/settings.js");

  const settings = loadSettings();
  const runManager = new RunManager(settings);
  const server = createWsServer({ port, runManager });

  // Render TUI
  const { render } = await import("ink");
  const React = await import("react");
  const { App } = await import("../tui/App.js");

  render(React.createElement(App, { url: `ws://localhost:${port}/ws/interactive` }));
}
```

- [ ] **Step 2: Test manually**

Run: `cd ts && npx tsx src/cli/index.ts tui`
Expected: TUI renders in terminal, connected to local WS server

- [ ] **Step 3: Commit**

---

## Phase 7: Custom Scenario Pipeline (Tasks 29–31)

### Task 29: Custom Scenario Loader

**Files:**
- Create: `ts/src/scenarios/custom-loader.ts`
- Test: `ts/tests/custom-loader.test.ts`

Load custom scenarios from `knowledge/_custom_scenarios/` and register them. Leverages the existing family-pipeline.ts and creator files.

- [ ] **Step 1: Write failing test**
- [ ] **Step 2: Implement scanner + dynamic import + registration**
- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

### Task 30: Natural Language → Scenario Creation (TUI flow)

**Files:**
- Modify: `ts/src/server/ws-server.ts` (handle `create_scenario` command)

Wire `CreateScenarioCmd` → family classifier → appropriate designer → spec → creator → register + send `scenario_ready` back to TUI.

- [ ] **Step 1: Implement create_scenario handler in WS server**
- [ ] **Step 2: Test with mock provider**
- [ ] **Step 3: Commit**

---

### Task 31: Intent Validation

**Files:**
- Create: `ts/src/scenarios/intent-validator.ts`
- Test: `ts/tests/intent-validator.test.ts`

**Python reference:** `autocontext/src/autocontext/scenarios/custom/family_pipeline.py` (intent validation)

Validates that a generated scenario matches the user's original intent before proceeding.

- [ ] **Step 1: Write failing test**
- [ ] **Step 2: Implement intent validator**
- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

---

## Phase 8: Advanced Features (Tasks 32–39)

### Task 32: Curator (Quality Gate)

**Python reference:** `autocontext/src/autocontext/agents/` (curator role)

Adds curator quality gate for playbook updates + periodic lesson consolidation.

- [ ] Implement curator role in orchestrator
- [ ] Add `<!-- CURATOR_DECISION: accept|reject|merge -->` marker parsing
- [ ] Add lesson consolidation (dedup, prune to maxLessons)
- [ ] Test + commit

### Task 33: Ecosystem Runner

**Python reference:** `autocontext/src/autocontext/loop/ecosystem_runner.py`

Multi-provider cycling across generations with divergence tracking.

- [ ] Implement EcosystemRunner
- [ ] Add CLI `ecosystem` command
- [ ] Test + commit

### Task 34: secure-exec Sandbox Integration

Replace Monty with secure-exec for sandboxed code strategy execution.

- [ ] Add `secure-exec` dependency
- [ ] Implement `SandboxExecutor` using `NodeRuntime`
- [ ] Wire into tournament for code strategies
- [ ] Test + commit

### Task 35: Cross-Run Inheritance

Load playbook/tools/lessons from prior runs of the same scenario.

- [ ] Implement snapshot save on run completion
- [ ] Implement snapshot load on run start
- [ ] Test + commit

### Task 36: Stagnation Detection

Detect score plateaus and trigger fresh starts.

- [ ] Implement stagnation detector (consecutive rollbacks, score plateau)
- [ ] Add distillation (keep top N lessons, reset playbook)
- [ ] Test + commit

### Task 37: Notifications

Webhook notifications for run events.

- [ ] Implement stdout, HTTP, Slack notifiers
- [ ] Wire into event emitter
- [ ] Test + commit

### Task 38: Dead-End Tracking

Track strategies that consistently fail to avoid re-exploring them.

- [ ] Implement dead-end registry
- [ ] Inject into competitor prompt as avoid-list
- [ ] Test + commit

### Task 39: Session Reports

Generate cross-session summary reports.

- [ ] Implement report generation
- [ ] Wire into run completion
- [ ] Test + commit

---

## Dependency Graph

```
Phase 1 (Foundation)
  Task 1: Config ─────────────────────────────────────────┐
  Task 2: Storage extensions ─────────────────────────────┤
  Task 3: Event stream emitter ──────────────────────────┤
  Task 4: Controller ────────────────────────────────────┤
                                                          │
Phase 2 (Scenarios)                                       │
  Task 5: ScenarioInterface ─────────────────────────────┤
  Task 6: Grid CTF ──────────── (depends on Task 5) ────┤
  Task 7: Registry ──────────── (depends on Task 5,6) ──┤
  Task 8: Elo ───────────────────────────────────────────┤
  Task 8b: Execution supervisor (depends on 5) ─────────┤
  Task 9: Tournament ────────── (depends on 5,8,8b) ────┤
                                                          │
Phase 3 (Knowledge)                                       │
  Task 10: Playbook ─────────────────────────────────────┤
  Task 10b: Artifact store ──────────────────────────────┤
  Task 11: Trajectory ──────── (depends on Task 2) ─────┤
  Task 12: Context budget ───────────────────────────────┤
                                                          │
Phase 4 (Agents)                                          │
  Task 13: Roles (incl. translator) ─────────────────────┤
  Task 14: Prompts ──────────── (depends on 5,10,11,12) ┤
  Task 15: Provider bridge + RetryProvider ──────────────┤
  Task 16: Model router ────────────────────────────────┤
  Task 17: Codex runtime ───────────────────────────────┤
  Task 18: Orchestrator ────── (depends on 13-16) ──────┤
                                                          │
Phase 5 (Loop)                                            │
  Task 19: Deterministic provider ───────────────────────┤
  Task 20: Backpressure (simple + trend) ────────────────┤
  Task 21: Generation runner ─ (depends on ALL above) ──┤
  Task 22: CLI run ──────────── (depends on 21) ────────┤
  Task 23: Integration smoke test (depends on 22) ──────┘

Phase 6 (Server + TUI) ─────── (depends on Phase 5)
  Tasks 24-28: independent of Phase 7-8

Phase 7 (Custom scenarios) ─── (depends on Phase 6)
Phase 8 (Advanced) ──────────── (independent, incremental)
```

## Explicitly Out of Scope (deferred to future work)

- **Analytics module** — run tracing, facet extraction, calibration, correlation tracking, rubric drift monitoring, aggregate analysis. The Python GenerationRunner imports heavily from `autocontext.analytics`. This is deferred until after the core loop works.
- **Othello scenario** — only grid_ctf is ported initially. Othello follows the same ScenarioInterface and can be added as a follow-up task.
- **Meta-optimizer** — harness meta-optimization is deferred.
- **Pi CLI / Pi RPC runtimes** — lower priority than Claude CLI and Codex CLI.
- **OpenClaw adapter** — external integration, deferred.
- **TSConfig JSX** — must be added to `tsconfig.json` BEFORE any TUI task. Task 26 (Bundle TUI) should verify this first.

---

## Parallelization Opportunities

Tasks within the same phase that have no dependencies on each other can be executed by parallel subagents:

- **Phase 1:** Tasks 1, 2, 3, 4 — all independent
- **Phase 2:** Tasks 5+8 (interface + elo) can parallel, then 6+7+9 depend on them
- **Phase 3:** Tasks 10, 11, 12 — all independent
- **Phase 4:** Tasks 13, 15, 16, 17 — all independent; 14 depends on 10-12; 18 depends on 13-16
- **Phase 8:** All tasks independent

---

## Package.json Changes

```json
{
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.27.1",
    "better-sqlite3": "^11.0.0",
    "ws": "^8.18.0",
    "zod": "^3.24.0"
  },
  "optionalDependencies": {
    "ink": "^5.1.0",
    "ink-text-input": "^6.0.0",
    "react": "^18.3.1",
    "secure-exec": "^0.1.0"
  },
  "devDependencies": {
    "@types/better-sqlite3": "^7.6.0",
    "@types/react": "^18.3.0",
    "@types/ws": "^8.5.0",
    "tsx": "^4.21.0",
    "typescript": "^5.7.0",
    "vitest": "^3.0.0"
  }
}
```

TUI deps (ink, react) are optional — lazy-imported only by `autoctx tui`. This keeps the core package lightweight for CI and headless environments. The `tui` command checks for ink at runtime and prints an install instruction if missing.

---

## Success Criteria

After all phases, these commands work from a fresh `npm install -g autoctx`:

```bash
# Headless run with Claude subscription
autoctx run --scenario grid_ctf --gens 3 --provider claude-cli

# Headless run with Anthropic API key
ANTHROPIC_API_KEY=sk-ant-... autoctx run --scenario grid_ctf --gens 5

# Interactive TUI
autoctx tui

# Quick test (deterministic, no API key)
autoctx run --scenario grid_ctf --gens 1 --provider deterministic

# Existing commands still work
autoctx judge -p "..." -o "..." -r "..."
autoctx improve -p "..." -o "..." -r "..."
autoctx serve
```

The TUI supports:
- `/run grid_ctf 10` — start a run
- `/create <description>` — create scenario from natural language
- Pause/resume
- Inject hints
- Override gate decisions
- Real-time event stream
