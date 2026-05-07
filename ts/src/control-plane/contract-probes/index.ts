export type DirectoryContractFailureKind = "unexpected-file" | "missing-file";

export interface DirectoryContractFailure {
  readonly kind: DirectoryContractFailureKind;
  readonly path: string;
  readonly message: string;
}

export interface DirectoryContractProbeInputs {
  readonly presentFiles: readonly string[];
  readonly requiredFiles: readonly string[];
  readonly allowedFiles: readonly string[];
  readonly ignoredPatterns?: readonly RegExp[];
}

export interface DirectoryContractProbeResult {
  readonly passed: boolean;
  readonly failures: readonly DirectoryContractFailure[];
}

export function probeDirectoryContract(
  inputs: DirectoryContractProbeInputs,
): DirectoryContractProbeResult {
  const presentFiles = inputs.presentFiles.filter((path) => !isIgnored(path, inputs.ignoredPatterns ?? []));
  const present = new Set(presentFiles);
  const allowed = new Set(inputs.allowedFiles);
  const failures: DirectoryContractFailure[] = [];

  for (const path of presentFiles) {
    if (!allowed.has(path)) {
      failures.push({
        kind: "unexpected-file",
        path,
        message: `unexpected file ${path}`,
      });
    }
  }

  for (const path of inputs.requiredFiles) {
    if (!present.has(path)) {
      failures.push({
        kind: "missing-file",
        path,
        message: `required file ${path} is missing`,
      });
    }
  }

  return {
    passed: failures.length === 0,
    failures,
  };
}

function isIgnored(path: string, ignoredPatterns: readonly RegExp[]): boolean {
  return ignoredPatterns.some((pattern) => pattern.test(path));
}
