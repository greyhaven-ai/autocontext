/**
 * Harness file versioning and persistence for TypeScript.
 * Port of autocontext/src/autocontext/storage/artifacts.py harness methods.
 */

import { z } from "zod";

import { assertSafeScenarioId } from "./scenario-id.js";
import {
  ensureSecureDirectory,
  listSecureDirectoryNames,
  readSecureTextFile,
  removeSecureFile,
  writeSecureTextFile,
} from "../security/secure-local-files.js";

export interface HarnessVersionEntry {
  version: number;
  generation: number;
}

export interface HarnessVersionMap {
  [name: string]: HarnessVersionEntry;
}

const MAX_SCENARIO_ID_CHARS = 128;
const MAX_HARNESS_NAME_CHARS = 128;
const MAX_HARNESS_SOURCE_BYTES = 1024 * 1024;
const MAX_VERSION_JSON_BYTES = 256 * 1024;
const MAX_HARNESS_FILES = 2_048;
const MAX_ARCHIVE_FILES = 10_000;
const VERSION_FILENAME = "harness_version.json";
const RESERVED_OBJECT_KEYS = new Set(["__proto__", "constructor", "prototype"]);

const HarnessVersionMapSchema = z.record(z.object({
  version: z.number().int().min(0),
  generation: z.number().int().min(0),
}));

export class HarnessStore {
  static readonly #VALID_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
  readonly #knowledgeRoot: string;
  readonly #scenarioName: string;

  constructor(knowledgeRoot: string, scenarioName: string) {
    const safeScenarioName = assertSafeScenarioId(scenarioName);
    if (safeScenarioName.length > MAX_SCENARIO_ID_CHARS) {
      throw new Error(`scenario exceeds ${MAX_SCENARIO_ID_CHARS} character limit`);
    }
    this.#knowledgeRoot = knowledgeRoot;
    this.#scenarioName = safeScenarioName;
  }

  /** List harness .py file names (without extension). */
  listHarness(): string[] {
    return listSecureDirectoryNames(
      this.#knowledgeRoot,
      this.#harnessComponents(),
      MAX_HARNESS_FILES,
    )
      .filter((filename) => filename.endsWith(".py"))
      .map((filename) => filename.slice(0, -3))
      .filter((name) => HarnessStore.#VALID_NAME.test(name))
      .sort();
  }

  #validateName(name: string): string {
    const normalized = name.trim();
    if (
      normalized.length > MAX_HARNESS_NAME_CHARS
      || RESERVED_OBJECT_KEYS.has(normalized)
      || !HarnessStore.#VALID_NAME.test(normalized)
    ) {
      throw new Error(`invalid harness name: ${name}`);
    }
    return normalized;
  }

  #harnessComponents(): string[] {
    return [this.#scenarioName, "harness"];
  }

  #archiveComponents(): string[] {
    return [...this.#harnessComponents(), "_archive"];
  }

  /** Read harness_version.json. */
  getVersions(): HarnessVersionMap {
    const raw = readSecureTextFile(
      this.#knowledgeRoot,
      this.#harnessComponents(),
      VERSION_FILENAME,
      MAX_VERSION_JSON_BYTES,
    );
    if (raw === null) return {};
    try {
      const versions = HarnessVersionMapSchema.parse(JSON.parse(raw));
      const entries = Object.entries(versions);
      if (entries.length > MAX_HARNESS_FILES) return {};
      if (entries.some(([name]) => (
        name.length > MAX_HARNESS_NAME_CHARS
        || RESERVED_OBJECT_KEYS.has(name)
        || !HarnessStore.#VALID_NAME.test(name)
      ))) {
        return {};
      }
      return versions;
    } catch {
      return {};
    }
  }

  /** Write a harness file with version tracking, archiving the previous. */
  writeVersioned(name: string, source: string, generation: number): string {
    const normalized = this.#validateName(name);
    if (!Number.isInteger(generation) || generation < 0) {
      throw new Error("harness generation must be a non-negative integer");
    }
    if (Buffer.byteLength(source, "utf-8") > MAX_HARNESS_SOURCE_BYTES) {
      throw new Error(`harness source exceeds ${MAX_HARNESS_SOURCE_BYTES} byte limit`);
    }
    ensureSecureDirectory(this.#knowledgeRoot, this.#harnessComponents());
    const filename = `${normalized}.py`;
    const currentSource = readSecureTextFile(
      this.#knowledgeRoot,
      this.#harnessComponents(),
      filename,
      MAX_HARNESS_SOURCE_BYTES,
    );
    const versions = this.getVersions();

    if (currentSource !== null) {
      const archiveVersions = this.#archiveVersions(normalized);
      const nextAvailableArchive = archiveVersions.length === 0
        ? 1
        : Math.max(...archiveVersions) + 1;
      const expectedArchive = Math.max(versions[normalized]?.version ?? 1, nextAvailableArchive);
      writeSecureTextFile(
        this.#knowledgeRoot,
        this.#archiveComponents(),
        `v${expectedArchive}_${normalized}.py`,
        currentSource,
        { maxBytes: MAX_HARNESS_SOURCE_BYTES, replace: false },
      );
    }

    const filePath = writeSecureTextFile(
      this.#knowledgeRoot,
      this.#harnessComponents(),
      filename,
      source,
      { maxBytes: MAX_HARNESS_SOURCE_BYTES, replace: true },
    );

    const prevVersion = versions[normalized]?.version ?? 0;
    versions[normalized] = { version: prevVersion + 1, generation };
    writeSecureTextFile(
      this.#knowledgeRoot,
      this.#harnessComponents(),
      VERSION_FILENAME,
      `${JSON.stringify(versions, null, 2)}\n`,
      { maxBytes: MAX_VERSION_JSON_BYTES, replace: true },
    );
    return filePath;
  }

  /** Rollback to the previous archived version. Returns content or null. */
  rollback(name: string): string | null {
    const normalized = this.#validateName(name);
    const entries = this.#archiveVersions(normalized);
    if (entries.length === 0) return null;
    const latestVersion = Math.max(...entries);
    const archiveFilename = `v${latestVersion}_${normalized}.py`;
    const content = readSecureTextFile(
      this.#knowledgeRoot,
      this.#archiveComponents(),
      archiveFilename,
      MAX_HARNESS_SOURCE_BYTES,
    );
    if (content === null) return null;

    const versions = this.getVersions();
    writeSecureTextFile(
      this.#knowledgeRoot,
      this.#harnessComponents(),
      `${normalized}.py`,
      content,
      { maxBytes: MAX_HARNESS_SOURCE_BYTES, replace: true },
    );
    const entry = versions[normalized];
    if (entry && entry.version > 1) {
      entry.version -= 1;
      writeSecureTextFile(
        this.#knowledgeRoot,
        this.#harnessComponents(),
        VERSION_FILENAME,
        `${JSON.stringify(versions, null, 2)}\n`,
        { maxBytes: MAX_VERSION_JSON_BYTES, replace: true },
      );
    }
    removeSecureFile(
      this.#knowledgeRoot,
      this.#archiveComponents(),
      archiveFilename,
    );
    return content;
  }

  /** Read a harness file's source code. */
  read(name: string): string | null {
    const normalized = this.#validateName(name);
    return readSecureTextFile(
      this.#knowledgeRoot,
      this.#harnessComponents(),
      `${normalized}.py`,
      MAX_HARNESS_SOURCE_BYTES,
    );
  }

  #archiveVersions(name: string): number[] {
    const archivePattern = new RegExp(`^v(\\d+)_${name}\\.py$`);
    return listSecureDirectoryNames(
      this.#knowledgeRoot,
      this.#archiveComponents(),
      MAX_ARCHIVE_FILES,
    )
      .map((filename) => archivePattern.exec(filename)?.[1])
      .filter((version): version is string => version !== undefined)
      .map((version) => Number.parseInt(version, 10))
      .filter((version) => Number.isSafeInteger(version) && version > 0);
  }
}
