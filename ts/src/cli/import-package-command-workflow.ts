import type { ConflictPolicy, ImportStrategyPackageResult } from "../knowledge/package.js";

export const IMPORT_PACKAGE_HELP_TEXT =
  "autoctx import-package --file <path> [--scenario <name>] [--conflict overwrite|merge|skip] [--json]";

export interface ImportPackageCommandValues {
  file?: string;
  scenario?: string;
  conflict?: string;
  json?: boolean;
}

export interface ImportPackageCommandPlan {
  file: string;
  scenarioOverride?: string;
  conflictPolicy: ConflictPolicy;
  json: boolean;
}

export function planImportPackageCommand(values: ImportPackageCommandValues): ImportPackageCommandPlan {
  if (!values.file) {
    throw new Error("Error: --file is required");
  }

  const conflict = (values.conflict ?? "overwrite") as ConflictPolicy;
  if (!(["overwrite", "merge", "skip"] as const).includes(conflict)) {
    throw new Error("Error: --conflict must be one of overwrite, merge, skip");
  }

  return {
    file: values.file,
    scenarioOverride: values.scenario,
    conflictPolicy: conflict,
    json: !!values.json,
  };
}

export function executeImportPackageCommandWorkflow<TArtifacts>(opts: {
  rawPackage: string;
  artifacts: TArtifacts;
  skillsRoot: string;
  scenarioOverride?: string;
  conflictPolicy: ConflictPolicy;
  importStrategyPackage: (args: {
    rawPackage: Record<string, unknown>;
    artifacts: TArtifacts;
    skillsRoot: string;
    scenarioOverride?: string;
    conflictPolicy: ConflictPolicy;
  }) => ImportStrategyPackageResult;
}): string {
  const result = opts.importStrategyPackage({
    rawPackage: JSON.parse(opts.rawPackage) as Record<string, unknown>,
    artifacts: opts.artifacts,
    skillsRoot: opts.skillsRoot,
    scenarioOverride: opts.scenarioOverride,
    conflictPolicy: opts.conflictPolicy,
  });
  return JSON.stringify(result, null, 2);
}
