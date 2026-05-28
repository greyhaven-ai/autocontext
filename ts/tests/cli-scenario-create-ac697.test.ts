/**
 * AC-697 slice 4: TS `autoctx scenario create` parity tests.
 *
 * Mirrors slice 3 on the TS side. `scenario` is registered as a
 * top-level command in `command-registry.ts`; `cmdScenario` in
 * `cli/index.ts` dispatches on the first sub-arg: `create` routes to
 * the existing `cmdNewScenario` handler by rewriting `process.argv`,
 * so the scaffolding logic stays single-sourced across the legacy
 * `new-scenario` alias and the canonical `scenario create` path.
 *
 * The behavioral check via spawnSync is gated on `bun` being
 * resolvable in PATH. Where a subprocess isn't viable, the
 * registry-level assertions in
 * `cli-contract-ac697.test.ts > "every yes-supported command is
 * registered in command-registry"` (now reaching multi-token paths
 * for the parent token) cover the parity invariant.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "vitest";

import { visibleSupportedCommandNames } from "../src/cli/command-registry.js";
import {
  NEW_SCENARIO_HELP_TEXT,
  SCENARIO_CREATE_HELP_TEXT,
  buildScenarioHelpText,
} from "../src/cli/new-scenario-command-workflow.js";

describe("AC-697 slice 4: `scenario` is registered + `scenario.create` flipped to yes", () => {
  test("`scenario` appears in visibleSupportedCommandNames()", () => {
    const registered = new Set(visibleSupportedCommandNames());
    expect(registered.has("scenario")).toBe(true);
    // `new-scenario` stays registered for backward compatibility as
    // the legacy alias the slice-1 contract pins on `scenario.create`.
    expect(registered.has("new-scenario")).toBe(true);
  });

  test("docs/cli-contract.json: TS `scenario.create` is yes", () => {
    const path = resolve(import.meta.dirname, "..", "..", "docs", "cli-contract.json");
    const contract = JSON.parse(readFileSync(path, "utf-8")) as {
      commands: { id: string; runtime_support: { typescript: { status: string } } }[];
    };
    const scenarioCreate = contract.commands.find((c) => c.id === "scenario.create");
    expect(scenarioCreate).toBeDefined();
    expect(scenarioCreate!.runtime_support.typescript.status).toBe("yes");
  });

  test("docs/cli-contract.json: `scenario.create` keeps `new-scenario` as its alias", () => {
    const path = resolve(import.meta.dirname, "..", "..", "docs", "cli-contract.json");
    const contract = JSON.parse(readFileSync(path, "utf-8")) as {
      commands: { id: string; aliases: string[] }[];
    };
    const scenarioCreate = contract.commands.find((c) => c.id === "scenario.create");
    expect(scenarioCreate).toBeDefined();
    expect(scenarioCreate!.aliases).toContain("new-scenario");
  });

  // PR #999 review (P3): the canonical help text must reflect the
  // canonical command name. The slice-4 delegation routed
  // `scenario create --help` through cmdNewScenario, which printed
  // the legacy `autoctx new-scenario --` header. The builder now
  // takes the command name and the two surfaces stay byte-identical
  // except for the header.
  test("buildScenarioHelpText renders the body once with a configurable command-name header", () => {
    const newScenarioBody = NEW_SCENARIO_HELP_TEXT.split("\n").slice(1).join("\n");
    const canonicalBody = SCENARIO_CREATE_HELP_TEXT.split("\n").slice(1).join("\n");
    expect(canonicalBody).toBe(newScenarioBody);
  });

  test("legacy help header still names `new-scenario`", () => {
    expect(NEW_SCENARIO_HELP_TEXT.split("\n")[0]).toBe("autoctx new-scenario — create a scenario");
  });

  test("canonical help header names `scenario create`", () => {
    expect(SCENARIO_CREATE_HELP_TEXT.split("\n")[0]).toBe(
      "autoctx scenario create — create a scenario",
    );
  });

  test("buildScenarioHelpText is the single source of truth for both surfaces", () => {
    expect(buildScenarioHelpText("new-scenario")).toBe(NEW_SCENARIO_HELP_TEXT);
    expect(buildScenarioHelpText("scenario create")).toBe(SCENARIO_CREATE_HELP_TEXT);
  });
});
