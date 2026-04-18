import { mkdirSync, openSync, closeSync, unlinkSync, writeSync } from "node:fs";
import { join } from "node:path";

export interface LockHandle {
  /** Release the lock. Idempotent: calling more than once is a no-op. */
  release(): void;
}

/**
 * Acquire an exclusive on-disk lock at `<registryRoot>/.autocontext/lock`.
 * Uses `O_CREAT | O_EXCL | O_WRONLY` semantics — if the lock file already
 * exists, this throws. Callers must call `release()` before exiting.
 *
 * Notes:
 *  - This is a hand-rolled, POSIX-style file-create lock. It is correct for
 *    cooperating processes that all use this primitive within a single host.
 *  - Stale locks from crashed processes are NOT auto-cleaned in v1; operators
 *    must manually remove `.autocontext/lock` if a process died holding it.
 */
export function acquireLock(registryRoot: string): LockHandle {
  const lockDir = join(registryRoot, ".autocontext");
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
