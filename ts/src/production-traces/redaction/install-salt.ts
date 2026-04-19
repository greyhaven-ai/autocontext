import { randomBytes } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";

/**
 * Install-salt management. The salt is a 256-bit (64 hex chars) random value
 * generated on first `autoctx production-traces init`. It is used as the
 * per-install hashing salt for user / session identifiers (spec §4.6) and as
 * the default `hashSalt` for category-override `"hash"` actions (§7.6).
 *
 * Storage: `<cwd>/.autocontext/install-salt` with file mode 0600.
 *
 * Rotation: `rotateInstallSalt` unconditionally overwrites the file. The CLI
 * is responsible for enforcing `--force`; this function does not.
 */

const ROOT = ".autocontext";
const FILE = "install-salt";

export function installSaltPath(cwd: string): string {
  return join(cwd, ROOT, FILE);
}

export async function loadInstallSalt(cwd: string): Promise<string | null> {
  const path = installSaltPath(cwd);
  if (!existsSync(path)) return null;
  const raw = await readFile(path, "utf-8");
  return raw.trim();
}

/**
 * Generate a fresh salt and write it to `.autocontext/install-salt`. Fails
 * with a clear message if the salt file already exists — callers must use
 * `rotateInstallSalt` (i.e. `autoctx production-traces rotate-salt --force`)
 * to replace an existing salt.
 */
export async function initializeInstallSalt(cwd: string): Promise<string> {
  const path = installSaltPath(cwd);
  if (existsSync(path)) {
    throw new Error(
      `install-salt already exists at ${path}; use 'autoctx production-traces rotate-salt --force' to replace it`,
    );
  }
  return writeSalt(path);
}

/**
 * Unconditionally generate and write a fresh salt, overwriting any existing
 * file. CLI should gate this behind `--force` per spec §4.6.
 */
export async function rotateInstallSalt(cwd: string): Promise<string> {
  return writeSalt(installSaltPath(cwd));
}

async function writeSalt(path: string): Promise<string> {
  const salt = randomBytes(32).toString("hex"); // 256 bits = 64 hex chars
  await mkdir(dirname(path), { recursive: true });
  // `mode` on writeFile is honored by Node's fs layer on POSIX; on Windows it
  // is a no-op which matches our POSIX-only permission test in the suite.
  await writeFile(path, salt + "\n", { encoding: "utf-8", mode: 0o600 });
  return salt;
}
