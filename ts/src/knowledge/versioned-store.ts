/**
 * Versioned file store with archive, prune, and rollback (AC-344 Task 10).
 * Mirrors Python's autocontext/harness/storage/versioned_store.py.
 */

import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, join } from "node:path";

export interface VersionedFileStoreOpts {
  maxVersions?: number;
  versionsDirName?: string;
  versionPrefix?: string;
  versionSuffix?: string;
}

export class VersionedFileStore {
  private root: string;
  private maxVersions: number;
  private versionsDirName: string;
  private versionPrefix: string;
  private versionSuffix: string;

  constructor(root: string, opts: VersionedFileStoreOpts = {}) {
    this.root = root;
    this.maxVersions = opts.maxVersions ?? 5;
    this.versionsDirName = opts.versionsDirName ?? ".versions";
    this.versionPrefix = opts.versionPrefix ?? "v";
    this.versionSuffix = opts.versionSuffix ?? ".txt";
  }

  private versionsDir(name: string): string {
    if (this.versionsDirName === ".versions") {
      return join(this.root, ".versions", name);
    }
    return join(this.root, this.versionsDirName);
  }

  private versionGlob(): { prefix: string; suffix: string } {
    return { prefix: this.versionPrefix, suffix: this.versionSuffix };
  }

  private versionPath(versionsDir: string, num: number): string {
    return join(versionsDir, `${this.versionPrefix}${String(num).padStart(4, "0")}${this.versionSuffix}`);
  }

  private nextVersionNumber(versionsDir: string): number {
    const versions = this.listVersionFiles(versionsDir);
    let maxVersion = 0;
    for (const path of versions) {
      const filename = basename(path);
      const core = filename.slice(
        this.versionPrefix.length,
        filename.length - this.versionSuffix.length,
      );
      const parsed = Number.parseInt(core, 10);
      if (Number.isFinite(parsed)) {
        maxVersion = Math.max(maxVersion, parsed);
      }
    }
    return maxVersion + 1;
  }

  private listVersionFiles(versionsDir: string): string[] {
    if (!existsSync(versionsDir)) return [];
    const { prefix, suffix } = this.versionGlob();
    return readdirSync(versionsDir)
      .filter((f) => f.startsWith(prefix) && f.endsWith(suffix))
      .sort()
      .map((f) => join(versionsDir, f));
  }

  write(name: string, content: string): void {
    const path = join(this.root, name);
    const versDir = this.versionsDir(name);

    if (existsSync(path)) {
      mkdirSync(versDir, { recursive: true });
      const existing = readFileSync(path, "utf-8");
      const nextNum = this.nextVersionNumber(versDir);
      writeFileSync(this.versionPath(versDir, nextNum), existing, "utf-8");
      this.prune(versDir);
    }

    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, content, "utf-8");
  }

  read(name: string, defaultValue = ""): string {
    const path = join(this.root, name);
    return existsSync(path) ? readFileSync(path, "utf-8") : defaultValue;
  }

  rollback(name: string): boolean {
    const versDir = this.versionsDir(name);
    if (!existsSync(versDir)) return false;
    const versions = this.listVersionFiles(versDir);
    if (versions.length === 0) return false;

    const latest = versions[versions.length - 1];
    const path = join(this.root, name);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, readFileSync(latest, "utf-8"), "utf-8");
    unlinkSync(latest);
    return true;
  }

  versionCount(name: string): number {
    return this.listVersionFiles(this.versionsDir(name)).length;
  }

  readVersion(name: string, version: number): string {
    const path = this.versionPath(this.versionsDir(name), version);
    return existsSync(path) ? readFileSync(path, "utf-8") : "";
  }

  private prune(versionsDir: string): void {
    const versions = this.listVersionFiles(versionsDir);
    while (versions.length > this.maxVersions) {
      unlinkSync(versions[0]);
      versions.shift();
    }
  }
}
