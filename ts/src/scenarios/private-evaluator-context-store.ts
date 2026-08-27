import { execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { channel } from "node:diagnostics_channel";
import {
  chmodSync,
  existsSync,
  linkSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  readlinkSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { hostname, uptime } from "node:os";
import { basename, dirname, join } from "node:path";
import { performance } from "node:perf_hooks";
import { isMainThread } from "node:worker_threads";

const PRIVATE_EVALUATOR_STORE_DIRECTORY = "_private_evaluator_context";
const PRIVATE_EVALUATOR_LOCK_DIRECTORY = "_private_evaluator_context_locks";
const PRIVATE_EVALUATOR_CONTEXT_VERSION = 1;
const EVALUATION_CONTEXT_REFERENCE_PATTERN = /^sha256:([a-f0-9]{64})$/;
const PRIVATE_WRITE_LOCK_TIMEOUT_MS = 5_000;
const PRIVATE_WRITE_LOCK_POLL_MS = 25;
const PRIVATE_WRITE_LOCK_ORPHAN_GRACE_MS = 5_000;
const PRIVATE_WRITE_LOCK_MAX_FUTURE_SKEW_MS = 60_000;
const MAX_PORTABLE_PID = 2_147_483_647;
const ACTIVE_PRIVATE_WRITE_LOCKS = new Set<string>();
const PRIVATE_WRITE_LOCK_WAIT_BUFFER = new Int32Array(new SharedArrayBuffer(4));
const PRIVATE_WRITE_LOCK_RECOVERY_SNAPSHOT_CHANNEL = channel(
  "autoctx.private-evaluator-context.write-lock.recovery-snapshot",
);
const PRIVATE_WRITE_LOCK_HOSTNAME = hostname();
const PRIVATE_WRITE_LOCK_SCOPE = currentPrivateEvaluatorWriteLockScope();
const PRIVATE_WRITE_LOCK_PROCESS_START_ID = readProcessStartIdentity(process.pid);

interface PrivateEvaluatorContextRecord {
  version: 1;
  scenarioName: string;
  evaluationContext: string;
}

interface PrivateEvaluatorWriteLockOwner {
  version: 2;
  token: string;
  pid: number;
  hostname: string;
  machineId: string;
  machineIdReliable: boolean;
  bootId: string;
  bootIdReliable: boolean;
  pidNamespaceId: string;
  pidNamespaceReliable: boolean;
  legacyScopeId: string;
  processStartId: string;
  processStartIdReliable: boolean;
  createdAtMs: number;
}

interface PrivateEvaluatorWriteLockScope {
  machineId: string;
  machineIdReliable: boolean;
  bootId: string;
  bootIdReliable: boolean;
  pidNamespaceId: string;
  pidNamespaceReliable: boolean;
  legacyScopeId: string;
  legacyScopeReliable: boolean;
}

interface ScopedLegacyPrivateEvaluatorWriteLockOwner
  extends LegacyPrivateEvaluatorWriteLockOwner {
  scopeId: string;
  processStartId: string;
}

interface LegacyPrivateEvaluatorWriteLockOwner {
  version: 1;
  token: string;
  pid: number;
  hostname: string;
  createdAtMs: number;
  scopeId?: unknown;
  processStartId?: unknown;
}

interface PrivateEvaluatorWriteLockSnapshot {
  identity: string;
  owner?: PrivateEvaluatorWriteLockOwner;
  scopedLegacyOwner?: ScopedLegacyPrivateEvaluatorWriteLockOwner;
  legacyOwner?: LegacyPrivateEvaluatorWriteLockOwner;
  invalidOwner: boolean;
  mtimeMs: number;
}

interface PrivateEvaluatorRecoveryClaim {
  path: string;
  recoveryRoot: string;
  token: string;
}

export class PrivateEvaluatorContextError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "PrivateEvaluatorContextError";
  }
}

export function evaluationContextReference(evaluationContext: string): string {
  return `sha256:${createHash("sha256").update(evaluationContext, "utf8").digest("hex")}`;
}

/**
 * Public-artifact and private-record updates form one fail-closed transaction.
 * Exclusive owner-file creation is atomic across processes, so two writers cannot
 * interleave public refs and private records for the same scenario.
 *
 * Automatic crash recovery requires a reliable machine identity and either the
 * same PID namespace or proof that the machine rebooted. Shared-volume deployments
 * that replace hosts/containers must verify the old owner has stopped and remove
 * its `.lock` file operationally; ambiguous ownership is intentionally fail closed.
 * Calls from `worker_threads` are rejected because a terminated worker leaves the
 * process PID alive and cannot provide a verifiable crash-owner identity.
 */
export function withPrivateEvaluatorContextWriteLock<T>(opts: {
  knowledgeRoot: string;
  scenarioName: string;
  write: () => T;
}): T {
  if (!isMainThread) {
    throw new PrivateEvaluatorContextError(
      "Private evaluator context persistence must run on the Node.js main thread so crash ownership remains process-verifiable",
    );
  }
  const lockRoot = join(opts.knowledgeRoot, PRIVATE_EVALUATOR_LOCK_DIRECTORY);
  const lockPath = join(lockRoot, `${privateEvaluatorScenarioKey(opts.scenarioName)}.lock`);
  mkdirSync(lockRoot, { recursive: true, mode: 0o700 });
  chmodSync(lockRoot, 0o700);
  if (ACTIVE_PRIVATE_WRITE_LOCKS.has(lockPath)) {
    throw new PrivateEvaluatorContextError(
      `Private evaluator context update is already in progress for saved task '${opts.scenarioName}'`,
    );
  }
  const owner: PrivateEvaluatorWriteLockOwner = {
    version: 2,
    token: randomUUID(),
    pid: process.pid,
    hostname: PRIVATE_WRITE_LOCK_HOSTNAME,
    machineId: PRIVATE_WRITE_LOCK_SCOPE.machineId,
    machineIdReliable: PRIVATE_WRITE_LOCK_SCOPE.machineIdReliable,
    bootId: PRIVATE_WRITE_LOCK_SCOPE.bootId,
    bootIdReliable: PRIVATE_WRITE_LOCK_SCOPE.bootIdReliable,
    pidNamespaceId: PRIVATE_WRITE_LOCK_SCOPE.pidNamespaceId,
    pidNamespaceReliable: PRIVATE_WRITE_LOCK_SCOPE.pidNamespaceReliable,
    legacyScopeId: PRIVATE_WRITE_LOCK_SCOPE.legacyScopeId,
    processStartId: PRIVATE_WRITE_LOCK_PROCESS_START_ID ?? "unknown",
    processStartIdReliable: PRIVATE_WRITE_LOCK_PROCESS_START_ID !== undefined,
    createdAtMs: Date.now(),
  };
  const deadline = performance.now() + PRIVATE_WRITE_LOCK_TIMEOUT_MS;
  for (;;) {
    try {
      createPrivateEvaluatorWriteLock(lockPath, owner);
      ACTIVE_PRIVATE_WRITE_LOCKS.add(lockPath);
      break;
    } catch (error) {
      if (errorCode(error) !== "EEXIST") throw error;
      if (recoverStalePrivateEvaluatorWriteLock(lockPath, deadline)) continue;
      if (performance.now() >= deadline) {
        throw new PrivateEvaluatorContextError(
          `Private evaluator context update is already in progress for saved task '${opts.scenarioName}'. `
          + "If this lock survived a host or container replacement, verify the old writer "
          + `has stopped before removing '${lockPath}'`,
          { cause: error },
        );
      }
      // The persistence APIs are synchronous. A short blocking wait lets a
      // concurrent process finish its transaction without permitting writes
      // to interleave; a crashed/stale lock times out fail closed.
      Atomics.wait(PRIVATE_WRITE_LOCK_WAIT_BUFFER, 0, 0, PRIVATE_WRITE_LOCK_POLL_MS);
    }
  }

  try {
    prunePrivateEvaluatorRecoveryArtifacts(lockPath);
    return opts.write();
  } finally {
    ACTIVE_PRIVATE_WRITE_LOCKS.delete(lockPath);
    releasePrivateEvaluatorWriteLock(lockPath, owner.token);
  }
}

function createPrivateEvaluatorWriteLock(
  lockPath: string,
  owner: PrivateEvaluatorWriteLockOwner,
): void {
  prunePrivateEvaluatorWriteLockTemps(lockPath);
  const temporaryPath = `${lockPath}.${owner.token}.tmp`;
  writeFileSync(temporaryPath, JSON.stringify(owner), {
    encoding: "utf8",
    flag: "wx",
    mode: 0o600,
  });
  try {
    // Publish only a fully written owner record. A process paused or killed
    // while creating the temporary file never exposes an empty/partial lock
    // that another contender could reap while the creator later enters.
    linkSync(temporaryPath, lockPath);
  } finally {
    try {
      rmSync(temporaryPath, { force: true });
    } catch {
      // The published owner is authoritative. A stranded temp is harmless and
      // is pruned by a later contender; cleanup must not strand the live lock.
    }
  }
}

function prunePrivateEvaluatorWriteLockTemps(lockPath: string): void {
  const parent = dirname(lockPath);
  const prefix = `${basename(lockPath)}.`;
  let entries: string[];
  try {
    entries = readdirSync(parent);
  } catch (error) {
    if (errorCode(error) === "ENOENT") return;
    throw error;
  }
  for (const entry of entries) {
    if (!entry.startsWith(prefix) || !entry.endsWith(".tmp")) continue;
    const temporaryPath = join(parent, entry);
    try {
      if (Date.now() - statSync(temporaryPath).mtimeMs < PRIVATE_WRITE_LOCK_ORPHAN_GRACE_MS) {
        continue;
      }
      rmSync(temporaryPath, { force: true });
    } catch (error) {
      if (errorCode(error) !== "ENOENT") throw error;
    }
  }
}

function releasePrivateEvaluatorWriteLock(lockPath: string, ownerToken: string): void {
  if (readPrivateEvaluatorWriteLockToken(lockPath) !== ownerToken) return;
  rmSync(lockPath, { force: true });
}

function readPrivateEvaluatorWriteLockToken(lockPath: string): string | undefined {
  try {
    const stats = statSync(lockPath);
    const ownerPath = stats.isDirectory() ? join(lockPath, "owner.json") : lockPath;
    const parsed: unknown = JSON.parse(readFileSync(ownerPath, "utf8"));
    if (!isRecord(parsed)) return undefined;
    return typeof parsed.token === "string" ? parsed.token : undefined;
  } catch (error) {
    if (errorCode(error) === "ENOENT" || error instanceof SyntaxError) return undefined;
    throw error;
  }
}

function recoverStalePrivateEvaluatorWriteLock(lockPath: string, deadline: number): boolean {
  if (performance.now() >= deadline) return false;
  const snapshot = readPrivateEvaluatorWriteLockSnapshot(lockPath);
  if (!snapshot) return true;
  if (!isStalePrivateEvaluatorWriteLock(snapshot, deadline)) return false;
  if (PRIVATE_WRITE_LOCK_RECOVERY_SNAPSHOT_CHANNEL.hasSubscribers) {
    try {
      PRIVATE_WRITE_LOCK_RECOVERY_SNAPSHOT_CHANNEL.publish({
        lockPath,
        identity: snapshot.identity,
      });
    } catch {
      // Diagnostics must never alter lock semantics.
    }
  }
  const claim = acquirePrivateEvaluatorRecoveryClaim(lockPath, snapshot.identity, deadline);
  if (!claim) return false;

  let identityCleared = false;
  try {
    // Only this exact claim token may act on the stale identity, so no delayed
    // contender can rename a replacement lock between confirmation and rename.
    const confirmed = readPrivateEvaluatorWriteLockSnapshot(lockPath);
    if (!confirmed) {
      identityCleared = true;
      return true;
    }
    if (confirmed.identity !== snapshot.identity) {
      identityCleared = true;
      return false;
    }
    const stalePath = `${lockPath}.stale.${randomUUID()}`;
    try {
      renameSync(lockPath, stalePath);
      identityCleared = true;
    } catch (error) {
      if (errorCode(error) === "ENOENT") {
        identityCleared = true;
        return true;
      }
      throw error;
    }
    rmSync(stalePath, { recursive: true, force: true });
    return true;
  } finally {
    try {
      releasePrivateEvaluatorWriteLock(claim.path, claim.token);
    } finally {
      // Once the stale identity is gone, cached contenders can only observe an
      // absent or replacement lock. It is then safe to prune all claim generations.
      if (identityCleared) prunePrivateEvaluatorRecoveryRoot(claim.recoveryRoot);
    }
  }
}

function prunePrivateEvaluatorRecoveryRoot(recoveryRoot: string): void {
  try {
    rmSync(recoveryRoot, {
      recursive: true,
      force: true,
      maxRetries: 3,
      retryDelay: PRIVATE_WRITE_LOCK_POLL_MS,
    });
  } catch {
    // Best-effort only. A cached contender may recreate the tree during
    // removal; the next successful owner sweeps any residue again.
  }
}

function prunePrivateEvaluatorRecoveryArtifacts(lockPath: string): void {
  const parent = dirname(lockPath);
  const base = basename(lockPath);
  let entries: string[];
  try {
    entries = readdirSync(parent);
  } catch {
    return;
  }
  for (const entry of entries) {
    if (!entry.startsWith(`${base}.recovery.`) && !entry.startsWith(`${base}.stale.`)) {
      continue;
    }
    prunePrivateEvaluatorRecoveryRoot(join(parent, entry));
  }
}

function acquirePrivateEvaluatorRecoveryClaim(
  lockPath: string,
  staleIdentity: string,
  deadline: number,
): PrivateEvaluatorRecoveryClaim | undefined {
  const identityDigest = createHash("sha256").update(staleIdentity, "utf8").digest("hex");
  const recoveryRoot = `${lockPath}.recovery.${identityDigest}`;
  if (!ensurePrivateEvaluatorRecoveryRoot(recoveryRoot, deadline)) return undefined;

  // A crashed claimant leaves an immutable generation. Exactly one contender
  // can publish the next generation, and a successful recovery prunes the chain.
  for (let generation = 1; ; generation += 1) {
    if (performance.now() >= deadline) return undefined;
    const claimPath = join(recoveryRoot, `${generation}.claim`);
    const owner: PrivateEvaluatorWriteLockOwner = {
      version: 2,
      token: randomUUID(),
      pid: process.pid,
      hostname: PRIVATE_WRITE_LOCK_HOSTNAME,
      machineId: PRIVATE_WRITE_LOCK_SCOPE.machineId,
      machineIdReliable: PRIVATE_WRITE_LOCK_SCOPE.machineIdReliable,
      bootId: PRIVATE_WRITE_LOCK_SCOPE.bootId,
      bootIdReliable: PRIVATE_WRITE_LOCK_SCOPE.bootIdReliable,
      pidNamespaceId: PRIVATE_WRITE_LOCK_SCOPE.pidNamespaceId,
      pidNamespaceReliable: PRIVATE_WRITE_LOCK_SCOPE.pidNamespaceReliable,
      legacyScopeId: PRIVATE_WRITE_LOCK_SCOPE.legacyScopeId,
      processStartId: PRIVATE_WRITE_LOCK_PROCESS_START_ID ?? "unknown",
      processStartIdReliable: PRIVATE_WRITE_LOCK_PROCESS_START_ID !== undefined,
      createdAtMs: Date.now(),
    };
    try {
      createPrivateEvaluatorWriteLock(claimPath, owner);
      return { path: claimPath, recoveryRoot, token: owner.token };
    } catch (error) {
      if (["EINVAL", "ENOENT", "ENOTDIR"].includes(errorCode(error) ?? "")) {
        // A successful competing recovery may prune this identity's claim
        // tree while a cached contender is publishing. Recreate and retry;
        // confirmation will observe that the stale identity is already gone.
        if (!ensurePrivateEvaluatorRecoveryRoot(recoveryRoot, deadline)) return undefined;
        generation = 0;
        continue;
      }
      if (errorCode(error) !== "EEXIST") throw error;
      const existingClaim = readPrivateEvaluatorWriteLockSnapshot(claimPath);
      if (!existingClaim) {
        generation -= 1;
        continue;
      }
      if (!isStalePrivateEvaluatorWriteLock(existingClaim, deadline)) return undefined;
    }
  }
}

function ensurePrivateEvaluatorRecoveryRoot(recoveryRoot: string, deadline: number): boolean {
  for (;;) {
    if (performance.now() >= deadline) return false;
    try {
      mkdirSync(recoveryRoot, { recursive: true, mode: 0o700 });
      chmodSync(recoveryRoot, 0o700);
      return true;
    } catch (error) {
      if (!["EINVAL", "ENOENT", "ENOTDIR"].includes(errorCode(error) ?? "")) throw error;
    }
  }
}

function readPrivateEvaluatorWriteLockSnapshot(
  lockPath: string,
): PrivateEvaluatorWriteLockSnapshot | undefined {
  try {
    const stats = statSync(lockPath);
    const ownerPath = stats.isDirectory() ? join(lockPath, "owner.json") : lockPath;
    let owner: PrivateEvaluatorWriteLockOwner | undefined;
    let scopedLegacyOwner: ScopedLegacyPrivateEvaluatorWriteLockOwner | undefined;
    let legacyOwner: LegacyPrivateEvaluatorWriteLockOwner | undefined;
    let invalidOwner = false;
    try {
      const parsed: unknown = JSON.parse(readFileSync(ownerPath, "utf8"));
      if (isPrivateEvaluatorWriteLockOwner(parsed)) owner = parsed;
      else if (isScopedLegacyPrivateEvaluatorWriteLockOwner(parsed)) {
        scopedLegacyOwner = parsed;
      }
      else if (isLegacyPrivateEvaluatorWriteLockOwner(parsed)) legacyOwner = parsed;
      else invalidOwner = true;
    } catch {
      owner = undefined;
      invalidOwner = true;
    }
    const recognizedOwner = owner ?? scopedLegacyOwner ?? legacyOwner;
    return {
      identity: recognizedOwner
        ? `owner:${recognizedOwner.token}`
        : `inode:${stats.dev}:${stats.ino}:${stats.size}:${stats.mtimeMs}`,
      ...(owner ? { owner } : {}),
      ...(scopedLegacyOwner ? { scopedLegacyOwner } : {}),
      ...(legacyOwner ? { legacyOwner } : {}),
      invalidOwner,
      mtimeMs: stats.mtimeMs,
    };
  } catch (error) {
    if (errorCode(error) === "ENOENT") return undefined;
    throw error;
  }
}

function isStalePrivateEvaluatorWriteLock(
  snapshot: PrivateEvaluatorWriteLockSnapshot,
  deadline?: number,
): boolean {
  const owner = snapshot.owner;
  if (owner) {
    // An owner from another boot, host, or PID namespace is ambiguous from this
    // process. Never evict it on a wall-clock lease: the protected callback is
    // synchronous and may legitimately outlive any fixed timeout.
    if (owner.machineId !== PRIVATE_WRITE_LOCK_SCOPE.machineId) return false;
    if (!owner.machineIdReliable || !PRIVATE_WRITE_LOCK_SCOPE.machineIdReliable) return false;
    // A process from an earlier boot of this same machine cannot still own the
    // lock, regardless of PID or namespace reuse.
    if (owner.bootId !== PRIVATE_WRITE_LOCK_SCOPE.bootId) {
      return owner.bootIdReliable && PRIVATE_WRITE_LOCK_SCOPE.bootIdReliable;
    }
    if (!owner.pidNamespaceReliable || !PRIVATE_WRITE_LOCK_SCOPE.pidNamespaceReliable) {
      return false;
    }
    // A different live-boot PID namespace may be a sibling container that this
    // process cannot observe. Keep that genuinely ambiguous owner fail closed.
    if (owner.pidNamespaceId !== PRIVATE_WRITE_LOCK_SCOPE.pidNamespaceId) return false;
    if (processIsZombie(owner.pid, deadline)) return true;
    const activeProcessStartId = readProcessStartIdentity(owner.pid, deadline);
    if (
      owner.processStartIdReliable
      && activeProcessStartId
      && processStartIdentitiesComparable(owner.processStartId, activeProcessStartId)
    ) {
      return activeProcessStartId !== owner.processStartId;
    }
    return !processIsAlive(owner.pid);
  }
  const scopedLegacyOwner = snapshot.scopedLegacyOwner;
  if (scopedLegacyOwner) {
    if (!PRIVATE_WRITE_LOCK_SCOPE.legacyScopeReliable) return false;
    if (scopedLegacyOwner.scopeId !== PRIVATE_WRITE_LOCK_SCOPE.legacyScopeId) return false;
    if (processIsZombie(scopedLegacyOwner.pid, deadline)) return true;
    const activeProcessStartId = readProcessStartIdentity(scopedLegacyOwner.pid, deadline);
    if (
      activeProcessStartId
      && processStartIdentitiesComparable(scopedLegacyOwner.processStartId, activeProcessStartId)
    ) {
      return activeProcessStartId !== scopedLegacyOwner.processStartId;
    }
    return !processIsAlive(scopedLegacyOwner.pid);
  }
  const legacyOwner = snapshot.legacyOwner;
  if (legacyOwner) {
    // Plain v1 owners cannot distinguish machines, boot sessions, or PID
    // namespaces. Even ESRCH is ambiguous across containers, so retain them
    // fail closed; scoped-v1 owners above remain safely recoverable.
    return false;
  }
  if (snapshot.invalidOwner) return false;
  return Date.now() - snapshot.mtimeMs >= PRIVATE_WRITE_LOCK_ORPHAN_GRACE_MS;
}

function processIsAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return errorCode(error) !== "ESRCH";
  }
}

function isPrivateEvaluatorWriteLockOwner(
  value: unknown,
): value is PrivateEvaluatorWriteLockOwner {
  return (
    isRecord(value) &&
    value.version === 2 &&
    typeof value.token === "string" &&
    value.token.length > 0 &&
    typeof value.pid === "number" &&
    Number.isInteger(value.pid) &&
    value.pid > 0 &&
    value.pid <= MAX_PORTABLE_PID &&
    typeof value.hostname === "string" &&
    value.hostname.length > 0 &&
    typeof value.machineId === "string" &&
    /^[a-f0-9]{64}$/.test(value.machineId) &&
    typeof value.machineIdReliable === "boolean" &&
    typeof value.bootId === "string" &&
    /^[a-f0-9]{64}$/.test(value.bootId) &&
    typeof value.bootIdReliable === "boolean" &&
    typeof value.pidNamespaceId === "string" &&
    /^[a-f0-9]{64}$/.test(value.pidNamespaceId) &&
    typeof value.pidNamespaceReliable === "boolean" &&
    typeof value.legacyScopeId === "string" &&
    /^[a-f0-9]{64}$/.test(value.legacyScopeId) &&
    typeof value.processStartId === "string" &&
    value.processStartId.length > 0 &&
    typeof value.processStartIdReliable === "boolean" &&
    typeof value.createdAtMs === "number" &&
    Number.isFinite(value.createdAtMs) &&
    value.createdAtMs >= 0 &&
    value.createdAtMs <= Date.now() + PRIVATE_WRITE_LOCK_MAX_FUTURE_SKEW_MS
  );
}

function isScopedLegacyPrivateEvaluatorWriteLockOwner(
  value: unknown,
): value is ScopedLegacyPrivateEvaluatorWriteLockOwner {
  return (
    isLegacyPrivateEvaluatorWriteLockOwner(value) &&
    typeof value.scopeId === "string" &&
    /^[a-f0-9]{64}$/.test(value.scopeId) &&
    typeof value.processStartId === "string" &&
    value.processStartId.length > 0
  );
}

function isLegacyPrivateEvaluatorWriteLockOwner(
  value: unknown,
): value is LegacyPrivateEvaluatorWriteLockOwner {
  return (
    isRecord(value) &&
    value.version === 1 &&
    typeof value.token === "string" &&
    value.token.length > 0 &&
    typeof value.pid === "number" &&
    Number.isInteger(value.pid) &&
    value.pid > 0 &&
    value.pid <= MAX_PORTABLE_PID &&
    typeof value.hostname === "string" &&
    value.hostname.length > 0 &&
    typeof value.createdAtMs === "number" &&
    Number.isFinite(value.createdAtMs) &&
    value.createdAtMs >= 0 &&
    value.createdAtMs <= Date.now() + PRIVATE_WRITE_LOCK_MAX_FUTURE_SKEW_MS
  );
}

function currentPrivateEvaluatorWriteLockScope(): PrivateEvaluatorWriteLockScope {
  const reliableBootIdentity = readReliableBootIdentity();
  const bootIdentity = reliableBootIdentity ?? "unknown-boot";
  const linuxMachineIdentity = readOptionalSystemText("/etc/machine-id");
  const reliableMachineIdentity = linuxMachineIdentity
    ?? readDarwinMachineIdentity()
    ?? readWindowsMachineIdentity();
  const machineIdentity = reliableMachineIdentity
    ?? `hostname:${PRIVATE_WRITE_LOCK_HOSTNAME}`;
  const legacyMachineIdentity = linuxMachineIdentity
    ?? PRIVATE_WRITE_LOCK_HOSTNAME;
  const linuxBootIdentity = readOptionalSystemText("/proc/sys/kernel/random/boot_id");
  const legacyBootIdentity = linuxBootIdentity
    ?? readLegacyBootMinute()
    ?? "unknown-boot";
  let pidNamespace = "native";
  let pidNamespaceReliable = process.platform !== "linux";
  try {
    pidNamespace = readlinkSync("/proc/self/ns/pid");
    pidNamespaceReliable = true;
  } catch {
    // Non-Linux hosts have one native PID namespace for this purpose.
  }
  return {
    machineId: privateEvaluatorScopeComponent(`${process.platform}\0${machineIdentity}`),
    machineIdReliable: reliableMachineIdentity !== undefined,
    bootId: privateEvaluatorScopeComponent(bootIdentity),
    bootIdReliable: reliableBootIdentity !== undefined,
    pidNamespaceId: privateEvaluatorScopeComponent(pidNamespace),
    pidNamespaceReliable,
    legacyScopeId: createHash("sha256")
      .update(
        `${process.platform}\0${legacyMachineIdentity}\0${legacyBootIdentity}\0${pidNamespace}`,
        "utf8",
      )
      .digest("hex"),
    legacyScopeReliable: (
      process.platform === "linux"
      && linuxMachineIdentity !== undefined
      && linuxBootIdentity !== undefined
      && pidNamespaceReliable
    ),
  };
}

function readReliableBootIdentity(): string | undefined {
  const linuxBootId = readOptionalSystemText("/proc/sys/kernel/random/boot_id");
  if (linuxBootId) return `linux-boot-id:${linuxBootId}`;
  if (process.platform !== "darwin") return undefined;
  try {
    const bootSession = execFileSync(
      "/usr/sbin/sysctl",
      ["-n", "kern.bootsessionuuid"],
      {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
        timeout: 1_000,
      },
    ).trim();
    return bootSession ? `darwin-boot-session:${bootSession}` : undefined;
  } catch {
    return undefined;
  }
}

function readLegacyBootMinute(): string | undefined {
  try {
    return `boot-minute:${Math.round((Date.now() - uptime() * 1_000) / 60_000)}`;
  } catch {
    return undefined;
  }
}

function privateEvaluatorScopeComponent(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function readDarwinMachineIdentity(): string | undefined {
  if (process.platform !== "darwin") return undefined;
  try {
    const platform = execFileSync(
      "/usr/sbin/ioreg",
      ["-rd1", "-c", "IOPlatformExpertDevice"],
      {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
        timeout: 1_000,
      },
    );
    return platform.match(/"IOPlatformUUID"\s*=\s*"([^"]+)"/)?.[1];
  } catch {
    return undefined;
  }
}

function readWindowsMachineIdentity(): string | undefined {
  if (process.platform !== "win32") return undefined;
  try {
    const registry = execFileSync(
      "reg.exe",
      [
        "query",
        "HKLM\\SOFTWARE\\Microsoft\\Cryptography",
        "/v",
        "MachineGuid",
      ],
      {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
        timeout: 1_000,
      },
    );
    return registry.match(/MachineGuid\s+REG_SZ\s+([^\r\n]+)/i)?.[1]?.trim();
  } catch {
    return undefined;
  }
}

function readOptionalSystemText(path: string): string | undefined {
  try {
    const value = readFileSync(path, "utf8").trim();
    return value || undefined;
  } catch {
    return undefined;
  }
}

function readProcessStartIdentity(pid: number, deadline?: number): string | undefined {
  if (!Number.isInteger(pid) || pid <= 0 || pid > MAX_PORTABLE_PID) return undefined;
  if (process.platform === "linux") {
    try {
      const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
      const commandEnd = stat.lastIndexOf(")");
      if (commandEnd >= 0) {
        const fields = stat.slice(commandEnd + 2).trim().split(/\s+/);
        const startTicks = fields[19];
        if (startTicks) return `linux-start-ticks:${startTicks}`;
      }
    } catch {
      // Fall back to ps when procfs is unavailable or races process exit.
    }
  }
  if (process.platform === "win32") return undefined;
  const remainingMs = deadline === undefined ? 1_000 : deadline - performance.now();
  if (remainingMs <= 0) return undefined;
  try {
    const startedAt = execFileSync("/bin/ps", ["-p", String(pid), "-o", "lstart="], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      timeout: Math.max(1, Math.min(1_000, remainingMs)),
      env: { LC_ALL: "C", PATH: process.env.PATH ?? "/usr/bin:/bin" },
    }).trim();
    return startedAt ? `ps-lstart:${startedAt}` : undefined;
  } catch {
    return undefined;
  }
}

function processIsZombie(pid: number, deadline?: number): boolean {
  if (!Number.isInteger(pid) || pid <= 0 || pid > MAX_PORTABLE_PID) return false;
  if (process.platform === "linux") {
    try {
      const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
      const commandEnd = stat.lastIndexOf(")");
      if (commandEnd >= 0) {
        return stat.slice(commandEnd + 2).trim().split(/\s+/, 1)[0] === "Z";
      }
    } catch {
      return false;
    }
  }
  if (process.platform === "win32") return false;
  const remainingMs = deadline === undefined ? 1_000 : deadline - performance.now();
  if (remainingMs <= 0) return false;
  try {
    const status = execFileSync("/bin/ps", ["-p", String(pid), "-o", "stat="], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      timeout: Math.max(1, Math.min(1_000, remainingMs)),
      env: { LC_ALL: "C", PATH: process.env.PATH ?? "/usr/bin:/bin" },
    }).trim();
    return status.startsWith("Z");
  } catch {
    return false;
  }
}

function processStartIdentitiesComparable(first: string, second: string): boolean {
  const firstSeparator = first.indexOf(":");
  const secondSeparator = second.indexOf(":");
  if (firstSeparator <= 0 || secondSeparator <= 0) return false;
  return first.slice(0, firstSeparator) === second.slice(0, secondSeparator);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorCode(error: unknown): string | undefined {
  if (!isRecord(error)) return undefined;
  return typeof error.code === "string" ? error.code : undefined;
}

export function persistPrivateEvaluatorContext(opts: {
  knowledgeRoot: string;
  scenarioName: string;
  evaluationContext?: string | null;
}): string | undefined {
  const evaluationContext = opts.evaluationContext?.trim() ? opts.evaluationContext : undefined;
  if (!evaluationContext) return undefined;

  const reference = evaluationContextReference(evaluationContext);
  const path = privateEvaluatorContextPath(
    opts.knowledgeRoot,
    opts.scenarioName,
    reference,
  );
  const storeDirectory = dirname(path);
  mkdirSync(storeDirectory, { recursive: true, mode: 0o700 });
  chmodSync(storeDirectory, 0o700);
  if (existsSync(path)) {
    loadPrivateEvaluatorContext({
      knowledgeRoot: opts.knowledgeRoot,
      scenarioName: opts.scenarioName,
      reference,
    });
    return reference;
  }
  const temporaryPath = `${path}.${process.pid}.${randomUUID()}.tmp`;
  const record: PrivateEvaluatorContextRecord = {
    version: PRIVATE_EVALUATOR_CONTEXT_VERSION,
    scenarioName: opts.scenarioName,
    evaluationContext,
  };

  try {
    writeFileSync(temporaryPath, JSON.stringify(record), {
      encoding: "utf8",
      flag: "wx",
      mode: 0o600,
    });
    try {
      // Hard-linking a fully written temporary file creates the immutable
      // content-addressed record atomically without replacing a concurrent or
      // previously persisted record.
      linkSync(temporaryPath, path);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      loadPrivateEvaluatorContext({
        knowledgeRoot: opts.knowledgeRoot,
        scenarioName: opts.scenarioName,
        reference,
      });
    }
    chmodSync(path, 0o600);
  } finally {
    rmSync(temporaryPath, { force: true });
  }

  return reference;
}

/**
 * Remove superseded records only after every public artifact has been written.
 * Until this point old public refs must remain resolvable across crashes.
 */
export function prunePrivateEvaluatorContexts(opts: {
  knowledgeRoot: string;
  scenarioName: string;
  keepReference?: string;
}): void {
  const scenarioDirectory = privateEvaluatorScenarioDirectory(
    opts.knowledgeRoot,
    opts.scenarioName,
  );
  if (!existsSync(scenarioDirectory)) return;
  const keepDigest = opts.keepReference
    ? parseEvaluationContextReference(opts.keepReference, opts.scenarioName)
    : undefined;
  for (const entry of readdirSync(scenarioDirectory)) {
    const match = /^([a-f0-9]{64})\.json$/.exec(entry);
    if (match?.[1] && match[1] === keepDigest) continue;
    rmSync(join(scenarioDirectory, entry), { recursive: true, force: true });
  }
  if (readdirSync(scenarioDirectory).length === 0) {
    rmSync(scenarioDirectory, { recursive: true, force: true });
  }
}

export function loadPrivateEvaluatorContext(opts: {
  knowledgeRoot: string;
  scenarioName: string;
  reference: string;
}): string {
  const expectedDigest = parseEvaluationContextReference(opts.reference, opts.scenarioName);
  const path = privateEvaluatorContextPath(
    opts.knowledgeRoot,
    opts.scenarioName,
    opts.reference,
  );
  if (!existsSync(path)) {
    throw new PrivateEvaluatorContextError(
      `Private evaluator context is missing for saved task '${opts.scenarioName}'`,
    );
  }

  let record: PrivateEvaluatorContextRecord;
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8")) as Partial<PrivateEvaluatorContextRecord>;
    if (
      parsed.version !== PRIVATE_EVALUATOR_CONTEXT_VERSION ||
      parsed.scenarioName !== opts.scenarioName ||
      typeof parsed.evaluationContext !== "string" ||
      parsed.evaluationContext.length === 0
    ) {
      throw new Error("invalid private evaluator record");
    }
    record = parsed as PrivateEvaluatorContextRecord;
  } catch (error) {
    throw new PrivateEvaluatorContextError(
      `Private evaluator context is unreadable for saved task '${opts.scenarioName}'`,
      { cause: error },
    );
  }

  const actualDigest = createHash("sha256")
    .update(record.evaluationContext, "utf8")
    .digest("hex");
  if (actualDigest !== expectedDigest) {
    throw new PrivateEvaluatorContextError(
      `Private evaluator context failed integrity verification for saved task '${opts.scenarioName}'`,
    );
  }
  return record.evaluationContext;
}

export function rehydratePersistedEvaluatorContext(opts: {
  knowledgeRoot: string;
  scenarioName: string;
  persistedSpec: Record<string, unknown>;
}): Record<string, unknown> {
  const hasCamelReference = Object.prototype.hasOwnProperty.call(
    opts.persistedSpec,
    "evaluationContextRef",
  );
  const hasSnakeReference = Object.prototype.hasOwnProperty.call(
    opts.persistedSpec,
    "evaluation_context_ref",
  );
  if (!hasCamelReference && !hasSnakeReference) {
    // Legacy plaintext specs remain readable. New writers never emit either
    // plaintext field, so this branch is compatibility-only.
    const hasLegacyPlaintext = [
      opts.persistedSpec.evaluationContext,
      opts.persistedSpec.evaluation_context,
    ].some((value) => typeof value === "string" && value.trim().length > 0);
    if (
      !hasLegacyPlaintext &&
      privateEvaluatorContextRecordsExist({
        knowledgeRoot: opts.knowledgeRoot,
        scenarioName: opts.scenarioName,
      })
    ) {
      throw new PrivateEvaluatorContextError(
        `Saved task '${opts.scenarioName}' has orphaned private evaluator context without a public reference`,
      );
    }
    return opts.persistedSpec;
  }
  const camelReference = hasCamelReference
    ? requiredReference(opts.persistedSpec.evaluationContextRef, opts.scenarioName)
    : undefined;
  const snakeReference = hasSnakeReference
    ? requiredReference(opts.persistedSpec.evaluation_context_ref, opts.scenarioName)
    : undefined;
  if (camelReference && snakeReference && camelReference !== snakeReference) {
    throw new PrivateEvaluatorContextError(
      `Private evaluator context references conflict for saved task '${opts.scenarioName}'`,
    );
  }
  const reference = camelReference ?? snakeReference;
  if (!reference) {
    throw new PrivateEvaluatorContextError(
      `Private evaluator context reference is invalid for saved task '${opts.scenarioName}'`,
    );
  }

  const evaluationContext = loadPrivateEvaluatorContext({
    knowledgeRoot: opts.knowledgeRoot,
    scenarioName: opts.scenarioName,
    reference,
  });
  return {
    ...withoutPersistedEvaluatorPlaintext(opts.persistedSpec),
    evaluation_context: evaluationContext,
  };
}

export function withoutPersistedEvaluatorPlaintext(
  persistedSpec: Record<string, unknown>,
): Record<string, unknown> {
  const sanitized = { ...persistedSpec };
  delete sanitized.evaluationContext;
  delete sanitized.evaluation_context;
  return sanitized;
}

export function knowledgeRootForScenarioDirectory(scenarioDirectory: string): string {
  const knowledgeRoot = tryKnowledgeRootForScenarioDirectory(scenarioDirectory);
  if (knowledgeRoot) return knowledgeRoot;
  throw new PrivateEvaluatorContextError(
    "Private evaluator context requires a scenario directory under _custom_scenarios",
  );
}

export function tryKnowledgeRootForScenarioDirectory(
  scenarioDirectory: string,
): string | undefined {
  const customScenarioDirectory = dirname(scenarioDirectory);
  if (basename(customScenarioDirectory) !== "_custom_scenarios") {
    return undefined;
  }
  return dirname(customScenarioDirectory);
}

export function privateEvaluatorContextRecordsExist(opts: {
  knowledgeRoot: string;
  scenarioName: string;
}): boolean {
  const scenarioDirectory = privateEvaluatorScenarioDirectory(
    opts.knowledgeRoot,
    opts.scenarioName,
  );
  return existsSync(scenarioDirectory) && readdirSync(scenarioDirectory).length > 0;
}

function privateEvaluatorScenarioDirectory(knowledgeRoot: string, scenarioName: string): string {
  return join(
    knowledgeRoot,
    PRIVATE_EVALUATOR_STORE_DIRECTORY,
    privateEvaluatorScenarioKey(scenarioName),
  );
}

function privateEvaluatorScenarioKey(scenarioName: string): string {
  return createHash("sha256").update(`scenario:${scenarioName}`, "utf8").digest("hex");
}

function privateEvaluatorContextPath(
  knowledgeRoot: string,
  scenarioName: string,
  reference: string,
): string {
  const digest = parseEvaluationContextReference(reference, scenarioName);
  return join(privateEvaluatorScenarioDirectory(knowledgeRoot, scenarioName), `${digest}.json`);
}

function parseEvaluationContextReference(reference: string, scenarioName: string): string {
  const match = EVALUATION_CONTEXT_REFERENCE_PATTERN.exec(reference);
  if (!match?.[1]) {
    throw new PrivateEvaluatorContextError(
      `Private evaluator context reference is invalid for saved task '${scenarioName}'`,
    );
  }
  return match[1];
}

function requiredReference(value: unknown, scenarioName: string): string {
  if (typeof value !== "string" || !EVALUATION_CONTEXT_REFERENCE_PATTERN.test(value)) {
    throw new PrivateEvaluatorContextError(
      `Private evaluator context reference is invalid for saved task '${scenarioName}'`,
    );
  }
  return value;
}
