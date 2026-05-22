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

// ----------------------------------------------------------------------------
// AC-728: cleanup contract probe
// ----------------------------------------------------------------------------
//
// Catches the leftover-artifact class of contract bugs the directory probe
// alone can miss: stray symlinks (broken or pointing outside an allowlist),
// stale lockfiles, OS / editor sidecars (.swp, ~, .DS_Store), and backup
// copies (.bak, .orig). The caller supplies a directory listing as
// CleanupFileEntry records carrying the metadata the probe needs (symlink
// status, mtime); the probe itself does no filesystem IO so it composes
// cleanly with the same trace-replay surfaces the AC-728 slice 1 probes
// already use.

export type CleanupContractFailureKind =
  | "stray-symlink"
  | "broken-symlink"
  | "stale-lockfile"
  | "stray-sidecar"
  | "stray-backup"
  | "missing-observation";

export interface CleanupContractFailure {
  readonly kind: CleanupContractFailureKind;
  readonly path: string;
  readonly message: string;
}

export interface CleanupFileEntry {
  readonly path: string;
  readonly isSymlink?: boolean;
  readonly symlinkTarget?: string;
  readonly symlinkBroken?: boolean;
  readonly mtime?: Date;
}

export interface CleanupContractProbeInputs {
  readonly entries: readonly CleanupFileEntry[];
  readonly now?: Date;
  readonly maxLockfileAgeMs?: number;
  readonly lockfilePatterns?: readonly RegExp[];
  readonly sidecarPatterns?: readonly RegExp[];
  readonly backupPatterns?: readonly RegExp[];
  readonly forbidSymlinks?: boolean;
  readonly allowedSymlinkTargets?: readonly string[];
  readonly ignoredPatterns?: readonly RegExp[];
}

export interface CleanupContractProbeResult {
  readonly passed: boolean;
  readonly failures: readonly CleanupContractFailure[];
}

const DEFAULT_LOCKFILE_PATTERNS: readonly RegExp[] = [/\.(lock|lck|pid)$/i];

// .swp / .swo are vim swap files; *~ is the emacs / generic editor backup
// suffix; .DS_Store is the macOS finder sidecar; .~lock.*# is LibreOffice's
// lock sidecar. Kept narrow on purpose so the default does not false-positive
// against legitimate dotfiles like .gitignore or .env.
const DEFAULT_SIDECAR_PATTERNS: readonly RegExp[] = [
  /\.sw[op]$/i,
  /~$/,
  /(^|\/)\.DS_Store$/,
  /(^|\/)\.~lock\..*#$/,
];

const DEFAULT_BACKUP_PATTERNS: readonly RegExp[] = [/\.(bak|orig)$/i];

export function probeCleanupContract(
  inputs: CleanupContractProbeInputs,
): CleanupContractProbeResult {
  const ignored = inputs.ignoredPatterns ?? [];
  const lockfilePatterns = inputs.lockfilePatterns ?? DEFAULT_LOCKFILE_PATTERNS;
  const sidecarPatterns = inputs.sidecarPatterns ?? DEFAULT_SIDECAR_PATTERNS;
  const backupPatterns = inputs.backupPatterns ?? DEFAULT_BACKUP_PATTERNS;
  const allowedSymlinkTargets = inputs.allowedSymlinkTargets;
  const now = inputs.now ?? new Date();
  const maxLockfileAgeMs = inputs.maxLockfileAgeMs;
  const failures: CleanupContractFailure[] = [];

  for (const entry of inputs.entries) {
    if (isIgnored(entry.path, ignored)) {
      continue;
    }

    if (entry.isSymlink) {
      if (entry.symlinkBroken) {
        failures.push({
          kind: "broken-symlink",
          path: entry.path,
          message: `${entry.path} is a broken symlink (target missing)`,
        });
        continue;
      }
      if (inputs.forbidSymlinks) {
        const target = entry.symlinkTarget ?? "<unknown>";
        failures.push({
          kind: "stray-symlink",
          path: entry.path,
          message: `${entry.path} is a symlink (target ${target}); symlinks are forbidden by contract`,
        });
        continue;
      }
      if (allowedSymlinkTargets !== undefined) {
        // PR #985 review lesson, retrofitted: a declared expectation
        // (allowedSymlinkTargets) without its observation (symlinkTarget)
        // must fail with missing-observation, not silently treat the
        // target as "<unknown>" and let a broken extractor satisfy the
        // allowlist contract.
        if (entry.symlinkTarget === undefined) {
          failures.push({
            kind: "missing-observation",
            path: entry.path,
            message: `${entry.path} is a symlink but no symlinkTarget was supplied; cannot evaluate allowedSymlinkTargets contract`,
          });
        } else if (!allowedSymlinkTargets.includes(entry.symlinkTarget)) {
          failures.push({
            kind: "stray-symlink",
            path: entry.path,
            message: `${entry.path} is a symlink to ${entry.symlinkTarget}; target is not in the allowlist`,
          });
        }
      }
      continue;
    }

    if (matchesAny(entry.path, lockfilePatterns)) {
      if (maxLockfileAgeMs === undefined) {
        // No age contract declared; the lockfile is flagged unconditionally.
        failures.push({
          kind: "stale-lockfile",
          path: entry.path,
          message: `${entry.path} is a leftover lockfile`,
        });
      } else if (entry.mtime === undefined) {
        // PR #985 review lesson, retrofitted: caller declared a
        // maxLockfileAgeMs contract; the lockfile entry without mtime
        // cannot satisfy it. Fail with missing-observation rather than
        // letting a stat-failing extractor pass the age contract by
        // omitting mtime.
        failures.push({
          kind: "missing-observation",
          path: entry.path,
          message: `${entry.path} matched a lockfile pattern but no mtime was supplied; cannot evaluate maxLockfileAgeMs contract`,
        });
      } else if (now.getTime() - entry.mtime.getTime() > maxLockfileAgeMs) {
        failures.push({
          kind: "stale-lockfile",
          path: entry.path,
          message: `${entry.path} is a lockfile older than ${maxLockfileAgeMs}ms`,
        });
      }
      continue;
    }

    if (matchesAny(entry.path, sidecarPatterns)) {
      failures.push({
        kind: "stray-sidecar",
        path: entry.path,
        message: `${entry.path} is an editor/OS sidecar leftover`,
      });
      continue;
    }

    if (matchesAny(entry.path, backupPatterns)) {
      failures.push({
        kind: "stray-backup",
        path: entry.path,
        message: `${entry.path} is a backup copy leftover`,
      });
      continue;
    }
  }

  return { passed: failures.length === 0, failures };
}

function matchesAny(path: string, patterns: readonly RegExp[]): boolean {
  return patterns.some((pattern) => pattern.test(path));
}

// ----------------------------------------------------------------------------
// AC-728: distributed / multi-process contract probe
// ----------------------------------------------------------------------------
//
// Closes the "distributed/multi-process parity checks beyond world-size 1"
// item from the AC-728 ticket. Distributed tensor code can pass shallow
// checks (process started, gradient computed locally) and still fail
// multi-rank parity: world size mismatch, missing rank, divergent gradient
// hash between ranks, or a rank running fewer steps than the others.
//
// The caller does the runtime IO (collect per-rank reports via
// torchrun / NCCL / MPI / whatever) and passes a `DistributedRankReport`
// per rank. The probe verifies the cross-rank invariants the caller
// declared. Pure function, no IO, same posture as the other AC-728 probes.
// Mirrors the PR #985 review lesson: a declared expectation without its
// observation must fail (missing-observation), not silently pass.

export type DistributedContractFailureKind =
  | "wrong-world-size"
  | "missing-rank"
  | "duplicate-rank"
  | "rank-divergence"
  | "wrong-step-count"
  | "missing-observation";

export interface DistributedContractFailure {
  readonly kind: DistributedContractFailureKind;
  readonly message: string;
  readonly rank?: number;
  readonly key?: string;
}

export interface DistributedRankReport {
  readonly rank: number;
  readonly steps?: number;
  readonly observations?: Readonly<Record<string, string>>;
}

export interface DistributedContractProbeInputs {
  readonly ranks: readonly DistributedRankReport[];
  readonly worldSize?: number;
  readonly expectedWorldSize?: number;
  readonly expectedSteps?: number;
  readonly mustMatchAcrossRanks?: readonly string[];
}

export interface DistributedContractProbeResult {
  readonly passed: boolean;
  readonly failures: readonly DistributedContractFailure[];
}

export function probeDistributedContract(
  inputs: DistributedContractProbeInputs,
): DistributedContractProbeResult {
  const failures: DistributedContractFailure[] = [];

  if (inputs.expectedWorldSize !== undefined) {
    if (inputs.worldSize === undefined) {
      failures.push({
        kind: "missing-observation",
        message: "declared expectation on worldSize but no observation was supplied",
      });
    } else if (inputs.worldSize !== inputs.expectedWorldSize) {
      failures.push({
        kind: "wrong-world-size",
        message: `observed world size ${inputs.worldSize} does not match expected ${inputs.expectedWorldSize}`,
      });
    }
  }

  // Track which ranks were seen so we can flag duplicates and missing ids.
  const seenRanks = new Map<number, DistributedRankReport>();
  for (const report of inputs.ranks) {
    if (seenRanks.has(report.rank)) {
      failures.push({
        kind: "duplicate-rank",
        rank: report.rank,
        message: `rank ${report.rank} reported more than once`,
      });
      continue;
    }
    seenRanks.set(report.rank, report);
  }

  // Missing-rank coverage only applies once we know the observed world size.
  if (inputs.worldSize !== undefined) {
    for (let r = 0; r < inputs.worldSize; r++) {
      if (!seenRanks.has(r)) {
        failures.push({
          kind: "missing-rank",
          rank: r,
          message: `rank ${r} did not report (world size ${inputs.worldSize})`,
        });
      }
    }
  }

  if (inputs.expectedSteps !== undefined) {
    for (const report of seenRanks.values()) {
      if (report.steps === undefined) {
        failures.push({
          kind: "missing-observation",
          rank: report.rank,
          message: `rank ${report.rank} declared step-count expectation but no steps observation was supplied`,
        });
      } else if (report.steps !== inputs.expectedSteps) {
        failures.push({
          kind: "wrong-step-count",
          rank: report.rank,
          message: `rank ${report.rank} ran ${report.steps} steps; expected ${inputs.expectedSteps}`,
        });
      }
    }
  }

  if (inputs.mustMatchAcrossRanks !== undefined && seenRanks.size > 0) {
    for (const key of inputs.mustMatchAcrossRanks) {
      // Collect the observation for `key` from every reporting rank; flag
      // any rank that did not report it, then flag divergence across the
      // values the rest produced.
      const valuesByRank = new Map<number, string>();
      let anyMissing = false;
      for (const report of seenRanks.values()) {
        const value = report.observations?.[key];
        if (value === undefined) {
          failures.push({
            kind: "missing-observation",
            rank: report.rank,
            key,
            message: `rank ${report.rank} did not report observation '${key}'`,
          });
          anyMissing = true;
          continue;
        }
        valuesByRank.set(report.rank, value);
      }
      if (anyMissing) {
        continue;
      }
      const distinctValues = new Set(valuesByRank.values());
      if (distinctValues.size > 1) {
        failures.push({
          kind: "rank-divergence",
          key,
          message: `ranks disagree on '${key}': observed distinct values ${[...distinctValues]
            .map((v) => JSON.stringify(v))
            .join(", ")}`,
        });
      }
    }
  }

  return { passed: failures.length === 0, failures };
}

// ----------------------------------------------------------------------------
// AC-728: media / tabular contract probe
// ----------------------------------------------------------------------------
//
// Closes the "media/data artifact dimensions, encoding, headers, and units"
// item from the AC-728 ticket. The caller pre-extracts whatever metadata it
// observed (header bytes, width / height, byte size, column metadata, line
// count); the probe verifies each declared expectation against its
// observation and reports the specific mismatch. Pure function with no IO,
// same posture as the other AC-728 probes. Per the PR #985 review: a
// declared expectation without its observation fails as
// missing-observation, not silently passes.

export type MediaContractFailureKind =
  | "wrong-magic-bytes"
  | "wrong-dimensions"
  | "wrong-byte-size"
  | "wrong-column-count"
  | "missing-column"
  | "wrong-line-count"
  | "missing-observation";

export interface MediaContractFailure {
  readonly kind: MediaContractFailureKind;
  readonly path: string;
  readonly message: string;
}

export interface MediaContractProbeInputs {
  readonly path: string;
  readonly headerBytes?: readonly number[];
  readonly expectedMagicBytes?: readonly number[];
  readonly width?: number;
  readonly height?: number;
  readonly expectedWidth?: number;
  readonly expectedHeight?: number;
  readonly byteSize?: number;
  readonly minByteSize?: number;
  readonly maxByteSize?: number;
  readonly columnCount?: number;
  readonly expectedColumnCount?: number;
  readonly columnNames?: readonly string[];
  readonly requiredColumnNames?: readonly string[];
  readonly lineCount?: number;
  readonly expectedLineCount?: number;
}

export interface MediaContractProbeResult {
  readonly passed: boolean;
  readonly failures: readonly MediaContractFailure[];
}

export function probeMediaContract(inputs: MediaContractProbeInputs): MediaContractProbeResult {
  const failures: MediaContractFailure[] = [];

  function missingObservation(field: string): void {
    failures.push({
      kind: "missing-observation",
      path: inputs.path,
      message: `${inputs.path} declared expectation on ${field} but no observation was supplied`,
    });
  }

  if (inputs.expectedMagicBytes !== undefined) {
    if (inputs.headerBytes === undefined) {
      missingObservation("headerBytes");
    } else {
      const expected = inputs.expectedMagicBytes;
      const header = inputs.headerBytes;
      const matched =
        header.length >= expected.length && expected.every((byte, index) => header[index] === byte);
      if (!matched) {
        failures.push({
          kind: "wrong-magic-bytes",
          path: inputs.path,
          message: `${inputs.path} header ${formatBytes(header.slice(0, expected.length))} does not match expected magic ${formatBytes(expected)}`,
        });
      }
    }
  }

  if (inputs.expectedWidth !== undefined) {
    if (inputs.width === undefined) {
      missingObservation("width");
    } else if (inputs.width !== inputs.expectedWidth) {
      failures.push({
        kind: "wrong-dimensions",
        path: inputs.path,
        message: `${inputs.path} width ${inputs.width} does not match expected ${inputs.expectedWidth}`,
      });
    }
  }

  if (inputs.expectedHeight !== undefined) {
    if (inputs.height === undefined) {
      missingObservation("height");
    } else if (inputs.height !== inputs.expectedHeight) {
      failures.push({
        kind: "wrong-dimensions",
        path: inputs.path,
        message: `${inputs.path} height ${inputs.height} does not match expected ${inputs.expectedHeight}`,
      });
    }
  }

  if (inputs.minByteSize !== undefined || inputs.maxByteSize !== undefined) {
    if (inputs.byteSize === undefined) {
      missingObservation("byteSize");
    } else {
      if (inputs.minByteSize !== undefined && inputs.byteSize < inputs.minByteSize) {
        failures.push({
          kind: "wrong-byte-size",
          path: inputs.path,
          message: `${inputs.path} byte size ${inputs.byteSize} is below minimum ${inputs.minByteSize}`,
        });
      }
      if (inputs.maxByteSize !== undefined && inputs.byteSize > inputs.maxByteSize) {
        failures.push({
          kind: "wrong-byte-size",
          path: inputs.path,
          message: `${inputs.path} byte size ${inputs.byteSize} is above maximum ${inputs.maxByteSize}`,
        });
      }
    }
  }

  if (inputs.expectedColumnCount !== undefined) {
    if (inputs.columnCount === undefined) {
      missingObservation("columnCount");
    } else if (inputs.columnCount !== inputs.expectedColumnCount) {
      failures.push({
        kind: "wrong-column-count",
        path: inputs.path,
        message: `${inputs.path} has ${inputs.columnCount} columns; expected ${inputs.expectedColumnCount}`,
      });
    }
  }

  if (inputs.requiredColumnNames !== undefined) {
    if (inputs.columnNames === undefined) {
      missingObservation("columnNames");
    } else {
      const observed = new Set(inputs.columnNames);
      for (const required of inputs.requiredColumnNames) {
        if (!observed.has(required)) {
          // Per-column failure path so a caller iterating failures can act
          // on the missing column name directly (mirrors
          // probeArtifactContract's missing-json-field convention).
          failures.push({
            kind: "missing-column",
            path: required,
            message: `${inputs.path} is missing required column ${JSON.stringify(required)}`,
          });
        }
      }
    }
  }

  if (inputs.expectedLineCount !== undefined) {
    if (inputs.lineCount === undefined) {
      missingObservation("lineCount");
    } else if (inputs.lineCount !== inputs.expectedLineCount) {
      failures.push({
        kind: "wrong-line-count",
        path: inputs.path,
        message: `${inputs.path} has ${inputs.lineCount} lines; expected ${inputs.expectedLineCount}`,
      });
    }
  }

  return { passed: failures.length === 0, failures };
}

function formatBytes(bytes: readonly number[]): string {
  return bytes.map((b) => b.toString(16).padStart(2, "0")).join(" ");
}

// ----------------------------------------------------------------------------
// AC-728: contract-probe suite runner (re-exports)
// ----------------------------------------------------------------------------

export {
  ContractProbeKindEnum,
  ContractProbeSuiteSchema,
  loadContractProbeSuite,
  runContractProbeSuite,
} from "./runner.js";
export type {
  ContractProbeFailure,
  ContractProbeInvocation,
  ContractProbeKind,
  ContractProbeRunResult,
  ContractProbeSuite,
  ContractProbeSuiteResult,
} from "./runner.js";
