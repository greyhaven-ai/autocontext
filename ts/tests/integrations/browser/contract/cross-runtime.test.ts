import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, test } from "vitest";

import {
  validateBrowserAction,
  validateBrowserAuditEvent,
  validateBrowserSessionConfig,
  validateBrowserSnapshot,
} from "../../../../src/integrations/browser/contract/validators.js";

const TS_ROOT = resolve(__dirname, "..", "..", "..", "..");
const WORKTREE_ROOT = resolve(TS_ROOT, "..");
const FIXTURES_DIR = resolve(__dirname, "..", "fixtures");
const PY_CWD = resolve(WORKTREE_ROOT, "autocontext");

type PythonResult = { valid: boolean; error?: string };
type PythonDictResult = { valid: boolean; errors: string[] };

function validatorForFixture(file: string) {
  if (file.includes("-session-config-")) return validateBrowserSessionConfig;
  if (file.includes("-action-")) return validateBrowserAction;
  if (file.includes("-snapshot-")) return validateBrowserSnapshot;
  if (file.includes("-audit-event-")) return validateBrowserAuditEvent;
  throw new Error(`unrecognized fixture file: ${file}`);
}

function pythonValidatorNameForFixture(file: string): string {
  if (file.includes("-session-config-")) return "validate_browser_session_config";
  if (file.includes("-action-")) return "validate_browser_action";
  if (file.includes("-snapshot-")) return "validate_browser_snapshot";
  if (file.includes("-audit-event-")) return "validate_browser_audit_event";
  throw new Error(`unrecognized fixture file: ${file}`);
}

function pythonDictValidatorNameForFixture(file: string): string {
  if (file.includes("-session-config-")) return "validate_browser_session_config_dict";
  if (file.includes("-action-")) return "validate_browser_action_dict";
  if (file.includes("-snapshot-")) return "validate_browser_snapshot_dict";
  if (file.includes("-audit-event-")) return "validate_browser_audit_event_dict";
  throw new Error(`unrecognized fixture file: ${file}`);
}

function runPythonValidate(validatorName: string, input: unknown): PythonResult {
  const script = [
    "import json, sys",
    "from pydantic import ValidationError",
    "from autocontext.integrations.browser.validate import (",
    "    validate_browser_action,",
    "    validate_browser_audit_event,",
    "    validate_browser_session_config,",
    "    validate_browser_snapshot,",
    ")",
    "validator_name = sys.argv[1]",
    "validator = globals()[validator_name]",
    "data = json.loads(sys.stdin.read())",
    "try:",
    "    doc = validator(data)",
    "    print(json.dumps({'valid': True, 'schemaVersion': doc.schemaVersion}))",
    "except ValidationError as e:",
    "    print(json.dumps({'valid': False, 'error': str(e)}))",
  ].join("\n");
  const result = spawnSync("uv", ["run", "python", "-c", script, validatorName], {
    cwd: PY_CWD,
    input: JSON.stringify(input),
    encoding: "utf-8",
    env: process.env,
  });
  if (result.status !== 0 && !result.stdout) {
    throw new Error(`python validate exited ${result.status}: ${result.stderr}`);
  }
  const line = result.stdout.trim().split("\n").pop() ?? "{}";
  return JSON.parse(line) as PythonResult;
}

function runPythonValidateDict(validatorName: string, input: unknown): PythonDictResult {
  const script = [
    "import json, sys",
    "from autocontext.integrations.browser.validate import (",
    "    validate_browser_action_dict,",
    "    validate_browser_audit_event_dict,",
    "    validate_browser_session_config_dict,",
    "    validate_browser_snapshot_dict,",
    ")",
    "validator_name = sys.argv[1]",
    "validator = globals()[validator_name]",
    "data = json.loads(sys.stdin.read())",
    "valid, errors = validator(data)",
    "print(json.dumps({'valid': valid, 'errors': errors}))",
  ].join("\n");
  const result = spawnSync("uv", ["run", "python", "-c", script, validatorName], {
    cwd: PY_CWD,
    input: JSON.stringify(input),
    encoding: "utf-8",
    env: process.env,
  });
  if (result.status !== 0 && !result.stdout) {
    throw new Error(`python validate exited ${result.status}: ${result.stderr}`);
  }
  const line = result.stdout.trim().split("\n").pop() ?? "{}";
  return JSON.parse(line) as PythonDictResult;
}

function hasUv(): boolean {
  const r = spawnSync("uv", ["--version"], { encoding: "utf-8" });
  return r.status === 0;
}

const maybeDescribe = hasUv() ? describe : describe.skip;

maybeDescribe("browser contract cross-runtime fixtures", () => {
  const fixtureFiles = readdirSync(FIXTURES_DIR).filter((f) => f.endsWith(".json")).sort();

  test("non-empty fixture set", () => {
    expect(fixtureFiles.length).toBeGreaterThanOrEqual(10);
  });

  for (const file of fixtureFiles) {
    const isInvalid = file.startsWith("invalid-");
    test(`${file}: TS and Python agree on ${isInvalid ? "rejection" : "acceptance"}`, () => {
      const body = readFileSync(resolve(FIXTURES_DIR, file), "utf-8");
      const data: unknown = JSON.parse(body);
      const tsResult = validatorForFixture(file)(data);
      const pyResult = runPythonValidate(pythonValidatorNameForFixture(file), data);
      const pyDictResult = runPythonValidateDict(pythonDictValidatorNameForFixture(file), data);

      expect(tsResult.valid).toBe(pyResult.valid);
      expect(tsResult.valid).toBe(pyDictResult.valid);
      expect(tsResult.valid).toBe(!isInvalid);
      if (isInvalid) {
        expect(pyDictResult.errors.length).toBeGreaterThan(0);
      }
    });
  }
});
