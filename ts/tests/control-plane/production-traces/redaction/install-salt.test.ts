import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  existsSync,
  statSync,
  writeFileSync,
  mkdirSync,
} from "node:fs";
import { tmpdir, platform } from "node:os";
import { join } from "node:path";
import {
  initializeInstallSalt,
  loadInstallSalt,
  rotateInstallSalt,
  installSaltPath,
} from "../../../../src/production-traces/redaction/install-salt.js";

describe("install salt management", () => {
  let cwd: string;

  beforeEach(() => {
    cwd = mkdtempSync(join(tmpdir(), "autocontext-install-salt-"));
  });

  afterEach(() => {
    rmSync(cwd, { recursive: true, force: true });
  });

  test("loadInstallSalt returns null when salt file does not exist", async () => {
    const salt = await loadInstallSalt(cwd);
    expect(salt).toBeNull();
  });

  test("initializeInstallSalt writes a 64-char hex salt (256 bits)", async () => {
    const salt = await initializeInstallSalt(cwd);
    expect(salt).toMatch(/^[0-9a-f]{64}$/);
    expect(existsSync(installSaltPath(cwd))).toBe(true);
  });

  test("loadInstallSalt returns the initialized salt", async () => {
    const initialSalt = await initializeInstallSalt(cwd);
    const loaded = await loadInstallSalt(cwd);
    expect(loaded).toBe(initialSalt);
  });

  test("initializeInstallSalt is idempotent-safe: refuses to overwrite existing salt", async () => {
    await initializeInstallSalt(cwd);
    await expect(initializeInstallSalt(cwd)).rejects.toThrow(/exists|rotate-salt/i);
  });

  test("rotateInstallSalt unconditionally generates new salt", async () => {
    const first = await initializeInstallSalt(cwd);
    const rotated = await rotateInstallSalt(cwd);
    expect(rotated).not.toBe(first);
    expect(rotated).toMatch(/^[0-9a-f]{64}$/);
    const reloaded = await loadInstallSalt(cwd);
    expect(reloaded).toBe(rotated);
  });

  test("rotateInstallSalt works when no salt file exists yet", async () => {
    const salt = await rotateInstallSalt(cwd);
    expect(salt).toMatch(/^[0-9a-f]{64}$/);
    expect(await loadInstallSalt(cwd)).toBe(salt);
  });

  test("initializeInstallSalt writes file with 0600 permissions (POSIX only)", async () => {
    if (platform() === "win32") return;
    await initializeInstallSalt(cwd);
    const st = statSync(installSaltPath(cwd));
    // Mask file-type bits; check rwx bits.
    expect(st.mode & 0o777).toBe(0o600);
  });

  test("loadInstallSalt returns trimmed content (tolerates trailing newline)", async () => {
    // Write a manually-formatted salt file to simulate hand-edited config.
    mkdirSync(join(cwd, ".autocontext"), { recursive: true });
    const hex = "a".repeat(64);
    writeFileSync(installSaltPath(cwd), hex + "\n");
    const loaded = await loadInstallSalt(cwd);
    expect(loaded).toBe(hex);
  });

  test("installSaltPath is under .autocontext/install-salt", () => {
    const p = installSaltPath(cwd);
    expect(p.endsWith(join(".autocontext", "install-salt"))).toBe(true);
  });
});
