import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { parseRunProgressReport, type RunProgressReport } from "./progress-report.js";

/** Shared loader used by CLI and cockpit/TUI inspection paths. */
export function loadRunProgressReport(options: {
  readonly knowledgeRoot: string;
  readonly runId: string;
  readonly scenario: string;
}): RunProgressReport | null {
  const path = join(
    resolve(options.knowledgeRoot),
    options.scenario,
    "progress_reports",
    `${options.runId}.json`,
  );
  if (!existsSync(path)) return null;
  try {
    return parseRunProgressReport(JSON.parse(readFileSync(path, "utf-8")));
  } catch {
    return null;
  }
}
