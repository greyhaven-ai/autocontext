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
  const presentFiles = inputs.presentFiles.filter(
    (path) => !isIgnored(path, inputs.ignoredPatterns ?? []),
  );
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

// ----------------------------------------------------------------------------
// AC-728: terminal contract probe
// ----------------------------------------------------------------------------

export type TerminalContractFailureKind =
  | "unexpected-exit-code"
  | "missing-stdout-pattern"
  | "forbidden-stdout-pattern"
  | "missing-stderr-pattern"
  | "forbidden-stderr-pattern";

export interface TerminalContractFailure {
  readonly kind: TerminalContractFailureKind;
  readonly message: string;
}

export interface TerminalContractProbeInputs {
  readonly exitCode: number;
  readonly stdout: string;
  readonly stderr: string;
  readonly expectedExitCode?: number;
  readonly requiredStdoutPatterns?: readonly RegExp[];
  readonly forbiddenStdoutPatterns?: readonly RegExp[];
  readonly requiredStderrPatterns?: readonly RegExp[];
  readonly forbiddenStderrPatterns?: readonly RegExp[];
}

export interface TerminalContractProbeResult {
  readonly passed: boolean;
  readonly failures: readonly TerminalContractFailure[];
}

export function probeTerminalContract(
  inputs: TerminalContractProbeInputs,
): TerminalContractProbeResult {
  const failures: TerminalContractFailure[] = [];
  const expectedExitCode = inputs.expectedExitCode ?? 0;
  if (inputs.exitCode !== expectedExitCode) {
    failures.push({
      kind: "unexpected-exit-code",
      message: `expected exit code ${expectedExitCode}, got ${inputs.exitCode}`,
    });
  }
  for (const pattern of inputs.requiredStdoutPatterns ?? []) {
    if (!pattern.test(inputs.stdout)) {
      failures.push({
        kind: "missing-stdout-pattern",
        message: `stdout did not match ${pattern}`,
      });
    }
  }
  for (const pattern of inputs.forbiddenStdoutPatterns ?? []) {
    if (pattern.test(inputs.stdout)) {
      failures.push({
        kind: "forbidden-stdout-pattern",
        message: `stdout matched forbidden ${pattern}`,
      });
    }
  }
  for (const pattern of inputs.requiredStderrPatterns ?? []) {
    if (!pattern.test(inputs.stderr)) {
      failures.push({
        kind: "missing-stderr-pattern",
        message: `stderr did not match ${pattern}`,
      });
    }
  }
  for (const pattern of inputs.forbiddenStderrPatterns ?? []) {
    if (pattern.test(inputs.stderr)) {
      failures.push({
        kind: "forbidden-stderr-pattern",
        message: `stderr matched forbidden ${pattern}`,
      });
    }
  }
  return { passed: failures.length === 0, failures };
}

// ----------------------------------------------------------------------------
// AC-728: service contract probe
// ----------------------------------------------------------------------------

export type ServiceEndpointProtocol = "tcp" | "udp";

export interface ServiceEndpointObservation {
  readonly host: string;
  readonly port: number;
  readonly protocol?: ServiceEndpointProtocol;
}

export type ServiceContractFailureKind =
  | "missing-endpoint"
  | "unexpected-endpoint"
  | "wrong-interface";

export interface ServiceContractFailure {
  readonly kind: ServiceContractFailureKind;
  readonly endpoint: ServiceEndpointObservation;
  readonly message: string;
}

export interface ServiceContractProbeInputs {
  readonly observed: readonly ServiceEndpointObservation[];
  readonly required: readonly ServiceEndpointObservation[];
  readonly allowed?: readonly ServiceEndpointObservation[];
}

export interface ServiceContractProbeResult {
  readonly passed: boolean;
  readonly failures: readonly ServiceContractFailure[];
}

function normalizeEndpoint(
  endpoint: ServiceEndpointObservation,
): Required<ServiceEndpointObservation> {
  return {
    host: endpoint.host,
    port: endpoint.port,
    protocol: endpoint.protocol ?? "tcp",
  };
}

function endpointKey(endpoint: ServiceEndpointObservation): string {
  const normalized = normalizeEndpoint(endpoint);
  return `${normalized.protocol}://${normalized.host}:${normalized.port}`;
}

function endpointMatchesAnyHost(
  required: ServiceEndpointObservation,
  observed: readonly ServiceEndpointObservation[],
): ServiceEndpointObservation | undefined {
  const requiredNorm = normalizeEndpoint(required);
  return observed.find((candidate) => {
    const norm = normalizeEndpoint(candidate);
    return norm.port === requiredNorm.port && norm.protocol === requiredNorm.protocol;
  });
}

export function probeServiceContract(
  inputs: ServiceContractProbeInputs,
): ServiceContractProbeResult {
  const failures: ServiceContractFailure[] = [];
  const observedKeys = new Set(inputs.observed.map(endpointKey));

  for (const required of inputs.required) {
    const requiredKey = endpointKey(required);
    if (observedKeys.has(requiredKey)) {
      continue;
    }
    // Same port/protocol but different host -> wrong-interface failure.
    const portMatch = endpointMatchesAnyHost(required, inputs.observed);
    if (portMatch !== undefined) {
      failures.push({
        kind: "wrong-interface",
        endpoint: required,
        message: `required ${endpointKey(required)} but observed ${endpointKey(portMatch)}`,
      });
    } else {
      failures.push({
        kind: "missing-endpoint",
        endpoint: required,
        message: `required endpoint ${endpointKey(required)} not observed`,
      });
    }
  }

  if (inputs.allowed !== undefined) {
    const allowedKeys = new Set(inputs.allowed.map(endpointKey));
    for (const observed of inputs.observed) {
      if (!allowedKeys.has(endpointKey(observed))) {
        failures.push({
          kind: "unexpected-endpoint",
          endpoint: observed,
          message: `observed endpoint ${endpointKey(observed)} not in allowed list`,
        });
      }
    }
  }

  return { passed: failures.length === 0, failures };
}

// ----------------------------------------------------------------------------
// AC-728: artifact contract probe
// ----------------------------------------------------------------------------

export type ArtifactContractFailureKind =
  | "missing-substring"
  | "forbidden-substring"
  | "wrong-line-ending"
  | "invalid-json"
  | "missing-json-field";

export interface ArtifactContractFailure {
  readonly kind: ArtifactContractFailureKind;
  readonly path: string;
  readonly message: string;
}

export interface ArtifactContractProbeInputs {
  readonly path: string;
  readonly content: string;
  readonly expectedLineEnding?: "lf" | "crlf";
  readonly requiredSubstrings?: readonly string[];
  readonly forbiddenSubstrings?: readonly string[];
  readonly requiredJsonFields?: readonly string[];
}

export interface ArtifactContractProbeResult {
  readonly passed: boolean;
  readonly failures: readonly ArtifactContractFailure[];
}

function readJsonDotPath(value: unknown, path: string): unknown {
  const segments = path.split(".");
  let cursor: unknown = value;
  for (const segment of segments) {
    if (cursor === null || typeof cursor !== "object") {
      return undefined;
    }
    cursor = (cursor as Record<string, unknown>)[segment];
    if (cursor === undefined) {
      return undefined;
    }
  }
  return cursor;
}

export function probeArtifactContract(
  inputs: ArtifactContractProbeInputs,
): ArtifactContractProbeResult {
  const failures: (ArtifactContractFailure & { path: string })[] = [];

  for (const required of inputs.requiredSubstrings ?? []) {
    if (!inputs.content.includes(required)) {
      failures.push({
        kind: "missing-substring",
        path: inputs.path,
        message: `${inputs.path} is missing required substring ${JSON.stringify(required)}`,
      });
    }
  }

  for (const forbidden of inputs.forbiddenSubstrings ?? []) {
    if (inputs.content.includes(forbidden)) {
      failures.push({
        kind: "forbidden-substring",
        path: inputs.path,
        message: `${inputs.path} contains forbidden substring ${JSON.stringify(forbidden)}`,
      });
    }
  }

  if (inputs.expectedLineEnding === "lf") {
    if (inputs.content.includes("\r\n")) {
      failures.push({
        kind: "wrong-line-ending",
        path: inputs.path,
        message: `${inputs.path} contains CRLF but LF was required`,
      });
    }
  } else if (inputs.expectedLineEnding === "crlf") {
    // Only fail if content has bare \n that isn't part of \r\n.
    if (/(?<!\r)\n/.test(inputs.content)) {
      failures.push({
        kind: "wrong-line-ending",
        path: inputs.path,
        message: `${inputs.path} contains bare LF but CRLF was required`,
      });
    }
  }

  const requiredJsonFields = inputs.requiredJsonFields ?? [];
  if (requiredJsonFields.length > 0) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(inputs.content);
    } catch (err) {
      failures.push({
        kind: "invalid-json",
        path: inputs.path,
        message: `${inputs.path} is not valid JSON: ${err instanceof Error ? err.message : String(err)}`,
      });
      return { passed: false, failures };
    }
    for (const field of requiredJsonFields) {
      if (readJsonDotPath(parsed, field) === undefined) {
        failures.push({
          kind: "missing-json-field",
          path: field,
          message: `${inputs.path} is missing required JSON field ${field}`,
        });
      }
    }
  }

  return { passed: failures.length === 0, failures };
}
