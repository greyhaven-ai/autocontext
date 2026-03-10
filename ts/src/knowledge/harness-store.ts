/**
 * Harness file versioning and persistence for TypeScript.
 * Port of mts/src/mts/storage/artifacts.py harness methods.
 */

import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync, copyFileSync } from "node:fs";
import { unlinkSync } from "node:fs";
import { join } from "node:path";

export interface HarnessVersionEntry {
  version: number;
  generation: number;
}

export interface HarnessVersionMap {
  [name: string]: HarnessVersionEntry;
}

export class HarnessStore {
  private readonly harnessDir: string;
  private readonly archiveDir: string;
  private readonly versionPath: string;
  private static readonly VALID_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

  constructor(knowledgeRoot: string, scenarioName: string) {
    this.harnessDir = join(knowledgeRoot, scenarioName, "harness");
    this.archiveDir = join(this.harnessDir, "_archive");
    this.versionPath = join(this.harnessDir, "harness_version.json");
  }

  /** List harness .py file names (without extension). */
  listHarness(): string[] {
    if (!existsSync(this.harnessDir)) return [];
    return readdirSync(this.harnessDir)
      .filter((f) => f.endsWith(".py"))
      .map((f) => f.replace(/\.py$/, ""))
      .sort();
  }

  private validateName(name: string): string {
    const normalized = name.trim();
    if (!HarnessStore.VALID_NAME.test(normalized)) {
      throw new Error(`invalid harness name: ${name}`);
    }
    return normalized;
  }

  /** Read harness_version.json. */
  getVersions(): HarnessVersionMap {
    if (!existsSync(this.versionPath)) return {};
    return JSON.parse(readFileSync(this.versionPath, "utf-8")) as HarnessVersionMap;
  }

  /** Write a harness file with version tracking, archiving the previous. */
  writeVersioned(name: string, source: string, generation: number): string {
    const normalized = this.validateName(name);
    mkdirSync(this.harnessDir, { recursive: true });
    const filePath = join(this.harnessDir, `${normalized}.py`);

    // Archive current version if exists
    if (existsSync(filePath)) {
      mkdirSync(this.archiveDir, { recursive: true });
      const versions = this.getVersions();
      const entry = versions[normalized];
      const vNum = entry ? entry.version : 1;
      const archivePath = join(this.archiveDir, `v${vNum}_${normalized}.py`);
      copyFileSync(filePath, archivePath);
    }

    writeFileSync(filePath, source, "utf-8");

    // Update version metadata
    const versions = this.getVersions();
    const prevVersion = versions[normalized]?.version ?? 0;
    versions[normalized] = { version: prevVersion + 1, generation };
    writeFileSync(this.versionPath, JSON.stringify(versions, null, 2), "utf-8");

    return filePath;
  }

  /** Rollback to the previous archived version. Returns content or null. */
  rollback(name: string): string | null {
    const normalized = this.validateName(name);
    if (!existsSync(this.archiveDir)) return null;

    // Find latest archive for this name
    const entries = readdirSync(this.archiveDir)
      .map((f) => {
        const match = f.match(new RegExp(`^v(\\d+)_${normalized}\\.py$`));
        return match ? { file: f, version: Number.parseInt(match[1], 10) } : null;
      })
      .filter((entry): entry is { file: string; version: number } => entry !== null);
    if (entries.length === 0) return null;
    entries.sort((a, b) => a.version - b.version);

    const latestArchive = entries[entries.length - 1].file;
    const archivePath = join(this.archiveDir, latestArchive);
    const content = readFileSync(archivePath, "utf-8");

    // Restore
    const filePath = join(this.harnessDir, `${normalized}.py`);
    writeFileSync(filePath, content, "utf-8");

    // Remove used archive
    unlinkSync(archivePath);

    // Update version metadata
    const versions = this.getVersions();
    const entry = versions[normalized];
    if (entry && entry.version > 1) {
      entry.version -= 1;
      writeFileSync(this.versionPath, JSON.stringify(versions, null, 2), "utf-8");
    }

    return content;
  }

  /** Read a harness file's source code. */
  read(name: string): string | null {
    const normalized = this.validateName(name);
    const filePath = join(this.harnessDir, `${normalized}.py`);
    if (!existsSync(filePath)) return null;
    return readFileSync(filePath, "utf-8");
  }
}
