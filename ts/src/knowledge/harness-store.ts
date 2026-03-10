/**
 * Harness file versioning and persistence for TypeScript.
 * Port of mts/src/mts/storage/artifacts.py harness methods.
 */

import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync, copyFileSync } from "node:fs";
import { join, basename } from "node:path";

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

  /** Read harness_version.json. */
  getVersions(): HarnessVersionMap {
    if (!existsSync(this.versionPath)) return {};
    return JSON.parse(readFileSync(this.versionPath, "utf-8")) as HarnessVersionMap;
  }

  /** Write a harness file with version tracking, archiving the previous. */
  writeVersioned(name: string, source: string, generation: number): string {
    mkdirSync(this.harnessDir, { recursive: true });
    const filePath = join(this.harnessDir, `${name}.py`);

    // Archive current version if exists
    if (existsSync(filePath)) {
      mkdirSync(this.archiveDir, { recursive: true });
      const versions = this.getVersions();
      const entry = versions[name];
      const vNum = entry ? entry.version : 1;
      const archivePath = join(this.archiveDir, `v${vNum}_${name}.py`);
      copyFileSync(filePath, archivePath);
    }

    writeFileSync(filePath, source, "utf-8");

    // Update version metadata
    const versions = this.getVersions();
    const prevVersion = versions[name]?.version ?? 0;
    versions[name] = { version: prevVersion + 1, generation };
    writeFileSync(this.versionPath, JSON.stringify(versions, null, 2), "utf-8");

    return filePath;
  }

  /** Rollback to the previous archived version. Returns content or null. */
  rollback(name: string): string | null {
    if (!existsSync(this.archiveDir)) return null;

    // Find latest archive for this name
    const archives = readdirSync(this.archiveDir)
      .filter((f) => f.endsWith(`_${name}.py`))
      .sort();
    if (archives.length === 0) return null;

    const latestArchive = archives[archives.length - 1];
    const archivePath = join(this.archiveDir, latestArchive);
    const content = readFileSync(archivePath, "utf-8");

    // Restore
    const filePath = join(this.harnessDir, `${name}.py`);
    writeFileSync(filePath, content, "utf-8");

    // Remove used archive
    const { unlinkSync } = require("node:fs") as typeof import("node:fs");
    unlinkSync(archivePath);

    // Update version metadata
    const versions = this.getVersions();
    const entry = versions[name];
    if (entry && entry.version > 1) {
      entry.version -= 1;
      writeFileSync(this.versionPath, JSON.stringify(versions, null, 2), "utf-8");
    }

    return content;
  }

  /** Read a harness file's source code. */
  read(name: string): string | null {
    const filePath = join(this.harnessDir, `${name}.py`);
    if (!existsSync(filePath)) return null;
    return readFileSync(filePath, "utf-8");
  }
}
