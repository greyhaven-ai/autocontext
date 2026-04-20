/**
 * A2-I Layer 1 — EditDescriptor ADT round-trip + schema validation for session + plan.
 *
 * Spec §4.2: WrapExpressionEdit, InsertStatementEdit, ReplaceExpressionEdit.
 * Spec §9.1 + §9.2: InstrumentSession and InstrumentPlan shapes.
 */
import { describe, test, expect } from "vitest";
import type {
  WrapExpressionEdit,
  InsertStatementEdit,
  ReplaceExpressionEdit,
  EditDescriptor,
  ImportSpec,
  SourceRange,
  InstrumentSession,
  InstrumentPlan,
} from "../../../../src/control-plane/instrument/contract/plugin-interface.js";
import {
  validateInstrumentSession,
  validateInstrumentPlan,
} from "../../../../src/control-plane/instrument/contract/validators.js";

const range: SourceRange = {
  startByte: 10,
  endByte: 20,
  startLineCol: { line: 2, col: 4 },
  endLineCol: { line: 2, col: 14 },
};

const imp: ImportSpec = {
  module: "autocontext.integrations.openai",
  name: "instrument_client",
  kind: "named",
};

describe("EditDescriptor ADT round-trip", () => {
  test("WrapExpressionEdit round-trips through JSON", () => {
    const edit: WrapExpressionEdit = {
      kind: "wrap-expression",
      pluginId: "openai-python",
      sourceFilePath: "src/agent.py",
      importsNeeded: [imp],
      notes: ["wraps OpenAI(...) construction in instrument_client"],
      range,
      wrapFn: "instrument_client",
      wrapArgsBefore: [],
      wrapArgsAfter: [],
    };
    const roundTripped = JSON.parse(JSON.stringify(edit)) as WrapExpressionEdit;
    expect(roundTripped).toEqual(edit);
    expect(roundTripped.kind).toBe("wrap-expression");
  });

  test("InsertStatementEdit round-trips through JSON", () => {
    const edit: InsertStatementEdit = {
      kind: "insert-statement",
      pluginId: "anthropic-ts",
      sourceFilePath: "src/chat.ts",
      importsNeeded: [imp],
      anchor: { kind: "after", range },
      statementSource: "client = instrument_client(client);",
    };
    const roundTripped = JSON.parse(JSON.stringify(edit)) as InsertStatementEdit;
    expect(roundTripped).toEqual(edit);
    expect(roundTripped.kind).toBe("insert-statement");
    expect(roundTripped.anchor.kind).toBe("after");
  });

  test("ReplaceExpressionEdit round-trips through JSON", () => {
    const edit: ReplaceExpressionEdit = {
      kind: "replace-expression",
      pluginId: "custom",
      sourceFilePath: "src/x.py",
      importsNeeded: [],
      range,
      replacementSource: "some_replacement",
    };
    const roundTripped = JSON.parse(JSON.stringify(edit)) as ReplaceExpressionEdit;
    expect(roundTripped).toEqual(edit);
    expect(roundTripped.kind).toBe("replace-expression");
  });

  test("EditDescriptor union narrows on `kind`", () => {
    const edits: EditDescriptor[] = [
      {
        kind: "wrap-expression",
        pluginId: "p",
        sourceFilePath: "f",
        importsNeeded: [],
        range,
        wrapFn: "w",
      },
      {
        kind: "insert-statement",
        pluginId: "p",
        sourceFilePath: "f",
        importsNeeded: [],
        anchor: { kind: "before", range },
        statementSource: "x",
      },
    ];
    for (const e of edits) {
      if (e.kind === "wrap-expression") {
        expect(e.wrapFn).toBe("w");
      } else if (e.kind === "insert-statement") {
        expect(e.anchor.kind).toBe("before");
      }
    }
  });
});

describe("InstrumentSession schema validation", () => {
  const validSession: InstrumentSession = {
    cwd: "/repo",
    flags: {
      mode: "dry-run",
      enhanced: false,
      maxFileBytes: 1_048_576,
      failIfEmpty: false,
      excludes: [],
      output: "pretty",
      force: false,
    },
    startedAt: "2026-04-17T12:00:00.000Z",
    endedAt: "2026-04-17T12:00:01.500Z",
    autoctxVersion: "0.4.3",
    registeredPlugins: [
      { id: "openai-python", version: "1.0.0", sdkName: "openai", language: "python" },
    ],
    gitignoreFingerprint: "sha256:" + "a".repeat(64),
  };

  test("valid session passes", () => {
    const r = validateInstrumentSession(validSession);
    expect(r.valid).toBe(true);
  });

  test("missing gitignoreFingerprint rejected", () => {
    const bad = { ...validSession } as unknown as Record<string, unknown>;
    delete bad.gitignoreFingerprint;
    const r = validateInstrumentSession(bad);
    expect(r.valid).toBe(false);
  });

  test("gitignoreFingerprint must match sha256 pattern", () => {
    const bad = { ...validSession, gitignoreFingerprint: "nope" };
    const r = validateInstrumentSession(bad);
    expect(r.valid).toBe(false);
  });

  test("unknown flag mode rejected", () => {
    const bad = { ...validSession, flags: { ...validSession.flags, mode: "full-send" } };
    const r = validateInstrumentSession(bad);
    expect(r.valid).toBe(false);
  });

  test("apply-branch mode with branch + commit validates", () => {
    const session: InstrumentSession = {
      ...validSession,
      flags: {
        ...validSession.flags,
        mode: "apply-branch",
        branch: "autocontext-instrument-20260417",
        commit: "Instrument LLM clients (autocontext v0.4.3)",
      },
    };
    expect(validateInstrumentSession(session).valid).toBe(true);
  });
});

describe("InstrumentPlan schema validation", () => {
  const validEdit: WrapExpressionEdit = {
    kind: "wrap-expression",
    pluginId: "openai-python",
    sourceFilePath: "src/agent.py",
    importsNeeded: [imp],
    range,
    wrapFn: "instrument_client",
  };

  const validPlan: InstrumentPlan = {
    schemaVersion: "1.0",
    edits: [validEdit],
    sourceFiles: [
      {
        path: "src/agent.py",
        language: "python",
        directivesSummary: { offLines: [] },
        hasSecretLiteral: false,
        existingImports: [{ module: "openai", names: ["OpenAI"] }],
      },
    ],
    conflictDecisions: [
      { filePath: "src/agent.py", decision: { kind: "accepted" } },
    ],
    safetyDecisions: [
      { filePath: "src/agent.py", decision: { kind: "allow" } },
    ],
  };

  test("valid plan passes", () => {
    const r = validateInstrumentPlan(validPlan);
    expect(r.valid).toBe(true);
  });

  test("plan with refuse safety decision validates", () => {
    const plan: InstrumentPlan = {
      ...validPlan,
      safetyDecisions: [
        {
          filePath: "src/agent.py",
          decision: { kind: "refuse", reason: "matched AWS access key pattern at line 42" },
        },
      ],
    };
    expect(validateInstrumentPlan(plan).valid).toBe(true);
  });

  test("plan with rejected-conflict validates", () => {
    const plan: InstrumentPlan = {
      ...validPlan,
      conflictDecisions: [
        {
          filePath: "src/agent.py",
          decision: {
            kind: "rejected-conflict",
            conflictingPluginIds: ["openai-python", "langchain-python"],
            reason: "overlapping wrap at range 10..20",
          },
        },
      ],
    };
    expect(validateInstrumentPlan(plan).valid).toBe(true);
  });

  test("edit kind 'banana' is rejected", () => {
    const plan = {
      ...validPlan,
      edits: [{ ...validEdit, kind: "banana" }],
    };
    expect(validateInstrumentPlan(plan).valid).toBe(false);
  });

  test("ImportSpec missing kind is rejected", () => {
    const plan = {
      ...validPlan,
      edits: [
        {
          ...validEdit,
          importsNeeded: [{ module: "m", name: "n" }],
        },
      ],
    };
    expect(validateInstrumentPlan(plan).valid).toBe(false);
  });

  test("schemaVersion pattern enforced", () => {
    const plan = { ...validPlan, schemaVersion: "1" };
    expect(validateInstrumentPlan(plan).valid).toBe(false);
  });
});
