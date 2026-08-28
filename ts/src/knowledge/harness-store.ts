/**
 * Harness file versioning and persistence for TypeScript.
 * Port of autocontext/src/autocontext/storage/artifacts.py harness methods.
 */

import { z } from "zod";

import { assertSafeScenarioId } from "./scenario-id.js";
import {
  countSecureDirectoryEntries,
  ensureSecureDirectory,
  hasSecureDirectoryEntry,
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
const MAX_HARNESS_DIRECTORY_ENTRIES = MAX_HARNESS_FILES + 2;
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
    const harnesses = listSecureDirectoryNames(
      this.#knowledgeRoot,
      this.#harnessComponents(),
      MAX_HARNESS_DIRECTORY_ENTRIES,
    )
      .filter((filename) => filename.endsWith(".py"))
      .map((filename) => filename.slice(0, -3))
      .filter((name) => HarnessStore.#VALID_NAME.test(name))
      .sort();
    if (harnesses.length > MAX_HARNESS_FILES) {
      throw new Error(`harness directory exceeds ${MAX_HARNESS_FILES} harness file limit`);
    }
    return harnesses;
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
    if (raw === null) return emptyVersionMap();
    let versions: HarnessVersionMap;
    try {
      versions = HarnessVersionMapSchema.parse(JSON.parse(raw));
    } catch {
      return emptyVersionMap();
    }
    const entries = Object.entries(versions);
    if (entries.length > MAX_HARNESS_FILES) {
      throw new Error(
        `harness version metadata exceeds ${MAX_HARNESS_FILES} entry limit`,
      );
    }
    if (entries.some(([name]) => (
      name.length > MAX_HARNESS_NAME_CHARS
      || RESERVED_OBJECT_KEYS.has(name)
      || !HarnessStore.#VALID_NAME.test(name)
    ))) {
      return emptyVersionMap();
    }
    return copyVersionMap(versions);
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
    const regularFiles = listSecureDirectoryNames(
      this.#knowledgeRoot,
      this.#harnessComponents(),
      MAX_HARNESS_DIRECTORY_ENTRIES,
    );
    const physicalHarnessFiles = regularFiles.filter((entry) => entry.endsWith(".py"));
    if (currentSource === null && physicalHarnessFiles.length >= MAX_HARNESS_FILES) {
      throw new Error(`harness directory reached ${MAX_HARNESS_FILES} harness file limit`);
    }
    const directoryEntries = countSecureDirectoryEntries(
      this.#knowledgeRoot,
      this.#harnessComponents(),
      MAX_HARNESS_DIRECTORY_ENTRIES,
    );
    let entriesToCreate = currentSource === null ? 1 : 0;
    if (!regularFiles.includes(VERSION_FILENAME)) entriesToCreate += 1;
    if (
      currentSource !== null
      && !hasSecureDirectoryEntry(
        this.#knowledgeRoot,
        this.#harnessComponents(),
        "_archive",
        MAX_HARNESS_DIRECTORY_ENTRIES,
      )
    ) {
      entriesToCreate += 1;
    }
    if (directoryEntries + entriesToCreate > MAX_HARNESS_DIRECTORY_ENTRIES) {
      throw new Error(
        `harness directory reached ${MAX_HARNESS_DIRECTORY_ENTRIES} entry limit`,
      );
    }
    const versions = this.getVersions();
    const previousEntry = ownVersionEntry(versions, normalized);
    const nextVersions = copyVersionMap(versions);
    nextVersions[normalized] = {
      version: (previousEntry?.version ?? 0) + 1,
      generation,
    };
    if (Object.keys(nextVersions).length > MAX_HARNESS_FILES) {
      throw new Error(`harness version metadata reached ${MAX_HARNESS_FILES} entry limit`);
    }
    // Validate the complete metadata update before archiving or replacing the
    // current source so an over-limit map cannot leave partially updated state.
    const serializedVersions = serializeVersionMap(nextVersions);

    if (currentSource !== null) {
      const archiveEntryCount = countSecureDirectoryEntries(
        this.#knowledgeRoot,
        this.#archiveComponents(),
        MAX_ARCHIVE_FILES,
      );
      if (archiveEntryCount >= MAX_ARCHIVE_FILES) {
        throw new Error(`harness archive reached ${MAX_ARCHIVE_FILES} entry limit`);
      }
      const archiveVersions = this.#archiveVersions(normalized);
      const nextAvailableArchive = archiveVersions.length === 0
        ? 1
        : Math.max(...archiveVersions) + 1;
      const expectedArchive = Math.max(previousEntry?.version ?? 1, nextAvailableArchive);
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

    writeSecureTextFile(
      this.#knowledgeRoot,
      this.#harnessComponents(),
      VERSION_FILENAME,
      serializedVersions,
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
    const entry = ownVersionEntry(versions, normalized);
    let serializedVersions: string | null = null;
    if (entry && entry.version > 1) {
      const nextVersions = copyVersionMap(versions);
      nextVersions[normalized] = { ...entry, version: entry.version - 1 };
      serializedVersions = serializeVersionMap(nextVersions);
    }
    writeSecureTextFile(
      this.#knowledgeRoot,
      this.#harnessComponents(),
      `${normalized}.py`,
      content,
      { maxBytes: MAX_HARNESS_SOURCE_BYTES, replace: true },
    );
    if (serializedVersions !== null) {
      writeSecureTextFile(
        this.#knowledgeRoot,
        this.#harnessComponents(),
        VERSION_FILENAME,
        serializedVersions,
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

function serializeVersionMap(versions: HarnessVersionMap): string {
  if (Object.keys(versions).length > MAX_HARNESS_FILES) {
    throw new Error(
      `harness version metadata exceeds ${MAX_HARNESS_FILES} entry limit`,
    );
  }
  const serialized = `${JSON.stringify(versions, null, 2)}\n`;
  if (Buffer.byteLength(serialized, "utf-8") > MAX_VERSION_JSON_BYTES) {
    throw new Error(
      `harness version metadata exceeds ${MAX_VERSION_JSON_BYTES} byte limit`,
    );
  }
  return serialized;
}

function emptyVersionMap(): HarnessVersionMap {
  return Object.create(null) as HarnessVersionMap;
}

function copyVersionMap(versions: HarnessVersionMap): HarnessVersionMap {
  return Object.assign(emptyVersionMap(), versions);
}

function ownVersionEntry(
  versions: HarnessVersionMap,
  name: string,
): HarnessVersionEntry | undefined {
  return Object.hasOwn(versions, name) ? versions[name] : undefined;
}
