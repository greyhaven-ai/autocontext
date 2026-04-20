import { mkdirSync, openSync, closeSync, unlinkSync, writeSync } from "node:fs";
import { join } from "node:path";

export interface LockHandle {
  /** Release the lock. Idempotent: calling more than once is a no-op. */
  release(): void;
}

/**
 * Acquire an exclusive on-disk lock at `<cwd>/.autocontext/lock`.
 *
 * This is a local, deliberately-duplicated implementation of the same lock
 * file used by Foundation B's control-plane registry
 * (`src/control-plane/registry/lock.ts`). Import discipline forbids reusing
 * that module from here — the registry is not a shared primitive. However,
 * both locks target the SAME path (`.autocontext/lock`) so the OS-level
 * `O_EXCL` guarantee provides single-writer coordination across Foundation A
 * (production-traces ingest) and Foundation B (control-plane promotion).
 *
 * Notes:
 *  - Hand-rolled, POSIX-style file-create lock. Correct for cooperating
 *    processes on a single host.
 *  - Stale locks from crashed processes are NOT auto-cleaned in v1; operators
 *    must manually remove `.autocontext/lock` if a process died holding it.
 */
export function acquireLock(cwd: string): LockHandle {
  const lockDir = join(cwd, ".autocontext");
  mkdirSync(lockDir, { recursive: true });
  const lockPath = join(lockDir, "lock");

  let fd: number;
  try {
    // wx = O_WRONLY | O_CREAT | O_EXCL — fails if file exists.
    fd = openSync(lockPath, "wx");
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "EEXIST") {
      throw new Error(`acquireLock: lock already held at ${lockPath}`);
    }
    throw err;
  }

  // Best-effort: write the pid so a human can identify a stale holder.
  try {
    writeSync(fd, String(process.pid));
  } catch {
    // ignore — informational only
  }

  let released = false;
  return {
    release(): void {
      if (released) return;
      released = true;
      try {
        closeSync(fd);
      } catch {
        // ignore — fd may already be closed
      }
      try {
        unlinkSync(lockPath);
      } catch {
        // ignore — file may already be gone
      }
    },
  };
}
