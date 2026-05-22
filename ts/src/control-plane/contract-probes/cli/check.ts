/**
 * `autoctx probes check` -- run a JSON-defined contract-probe suite.
 *
 * In-process handler matching the production-traces / instrument CLI
 * pattern: parses args, loads the suite via `loadContractProbeSuite`,
 * runs it via `runContractProbeSuite`, and returns
 * `{ stdout, stderr, exitCode }`. The outer CLI adapter handles the
 * actual stdout / stderr / exit. Tests consume this runner directly.
 */

import { parseArgs } from "node:util";
import { z } from "zod";

import { loadContractProbeSuite, runContractProbeSuite } from "../index.js";
import type { ContractProbeSuiteResult } from "../index.js";

export interface ProbesCheckResult {
  readonly stdout: string;
  readonly stderr: string;
  readonly exitCode: number;
}

export const CHECK_HELP_TEXT = `autoctx probes check -- run a contract-probe suite against observed harness state.

Usage:
  autoctx probes check --suite <path> [--json]
  autoctx probes check --help

Options:
  --suite <path>   Path to a JSON probe suite (validated against
                   ContractProbeSuiteSchema). Required.
  --json           Emit a structured JSON report instead of human-readable
                   text. The JSON shape is:
                     {
                       "passed": boolean,
                       "results": [
                         {
                           "kind": <probe kind>,
                           "label": <optional caller-supplied attribution>,
                           "passed": boolean,
                           "failures": [ { "kind", "message", ... } ]
                         },
                         ...
                       ]
                     }
  -h, --help       Show this help text.

Exit codes:
  0   every probe in the suite passed.
  1   at least one probe failed, or the suite failed to load / parse.

The JSON suite-file format (with a minimal example and the seven
probe kinds) is documented under the "Contract Probes" section of
the autoctx README. Every input field that the suite declares an
expectation about must carry a corresponding observation; missing
observations fail with kind \`missing-observation\` rather than
silently passing.
`;

interface ParsedArgs {
  readonly suitePath?: string;
  readonly json: boolean;
  readonly help: boolean;
}

function parseCheckArgs(args: readonly string[]): ParsedArgs | { readonly error: string } {
  try {
    const { values } = parseArgs({
      args: [...args],
      options: {
        suite: { type: "string" },
        json: { type: "boolean", default: false },
        help: { type: "boolean", short: "h", default: false },
      },
      allowPositionals: false,
    });
    return {
      suitePath: typeof values.suite === "string" ? values.suite : undefined,
      json: values.json === true,
      help: values.help === true,
    };
  } catch (err) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
}

function renderTextReport(result: ContractProbeSuiteResult): string {
  const lines: string[] = [];
  lines.push(result.passed ? "probes check: PASS" : "probes check: FAIL");
  for (const probe of result.results) {
    const label = probe.label ? ` [${probe.label}]` : "";
    const status = probe.passed ? "pass" : "fail";
    lines.push(`  ${probe.kind}${label}: ${status}`);
    if (!probe.passed) {
      for (const failure of probe.failures) {
        lines.push(`    - ${failure.kind}: ${failure.message}`);
      }
    }
  }
  return lines.join("\n");
}

export async function runCheck(args: readonly string[]): Promise<ProbesCheckResult> {
  const parsed = parseCheckArgs(args);
  if ("error" in parsed) {
    return {
      stdout: "",
      stderr: `autoctx probes check: ${parsed.error}\n\n${CHECK_HELP_TEXT}`,
      exitCode: 1,
    };
  }

  if (parsed.help) {
    return { stdout: CHECK_HELP_TEXT, stderr: "", exitCode: 0 };
  }

  if (parsed.suitePath === undefined || parsed.suitePath.length === 0) {
    return {
      stdout: "",
      stderr: `autoctx probes check: --suite <path> is required\n\n${CHECK_HELP_TEXT}`,
      exitCode: 1,
    };
  }

  let suite;
  try {
    suite = loadContractProbeSuite(parsed.suitePath);
  } catch (err) {
    if (err instanceof z.ZodError) {
      // Surface every Zod issue line by line; the JSON spec failed
      // validation (typo, unknown key, malformed regex / date) and the
      // operator needs the full list to fix it.
      const issues = err.issues
        .map((issue) => {
          const path = issue.path.length > 0 ? issue.path.join(".") : "<root>";
          return `  - ${path}: ${issue.message}`;
        })
        .join("\n");
      return {
        stdout: "",
        stderr: `autoctx probes check: suite validation failed\n${issues}`,
        exitCode: 1,
      };
    }
    const message = err instanceof Error ? err.message : String(err);
    return {
      stdout: "",
      stderr: `autoctx probes check: failed to load suite from ${parsed.suitePath}: ${message}`,
      exitCode: 1,
    };
  }

  const result = runContractProbeSuite(suite);
  if (parsed.json) {
    return {
      stdout: JSON.stringify(result, null, 2),
      stderr: "",
      exitCode: result.passed ? 0 : 1,
    };
  }

  return {
    stdout: renderTextReport(result),
    stderr: "",
    exitCode: result.passed ? 0 : 1,
  };
}
