import { randomBytes } from "node:crypto";
import {
  closeSync,
  constants,
  fstatSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  openSync,
  readSync,
  readdirSync,
  realpathSync,
  renameSync,
  type Dirent,
  type Stats,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { isAbsolute, parse, relative, resolve } from "node:path";

const MAX_PATH_COMPONENT_CHARS = 255;
const READ_CHUNK_BYTES = 64 * 1024;

interface PathIdentity {
  path: string;
  dev: number;
  ino: number;
}

interface SecureDirectory {
  root: string;
  canonicalRoot: string;
  path: string;
  canonicalPath: string;
  identities: PathIdentity[];
}

export interface SecureWriteOptions {
  maxBytes: number;
  replace?: boolean;
}

function lstatOrNull(path: string): Stats | null {
  try {
    return lstatSync(path);
  } catch (error) {
    if (errorCode(error) === "ENOENT") return null;
    throw error;
  }
}

function errorCode(error: unknown): string | undefined {
  if (typeof error !== "object" || error === null || !("code" in error)) return undefined;
  return typeof error.code === "string" ? error.code : undefined;
}

function validateComponent(component: string): void {
  if (
    !component
    || component === "."
    || component === ".."
    || component.length > MAX_PATH_COMPONENT_CHARS
    || component.includes("/")
    || component.includes("\\")
    || component.includes("\0")
  ) {
    throw new Error(`unsafe filesystem path component: ${JSON.stringify(component)}`);
  }
}

function isContained(root: string, candidate: string, allowEqual = false): boolean {
  const child = relative(root, candidate);
  if (child === "") return allowEqual;
  return !child.startsWith("..") && !isAbsolute(child);
}

function directoryIdentity(path: string, stat: Stats): PathIdentity {
  if (stat.isSymbolicLink()) {
    throw new Error(`refusing symbolic-link directory: ${path}`);
  }
  if (!stat.isDirectory()) {
    throw new Error(`expected directory path: ${path}`);
  }
  return { path, dev: stat.dev, ino: stat.ino };
}

function resolveSecureDirectory(
  rootPath: string,
  components: readonly string[],
  create: boolean,
): SecureDirectory | null {
  if (!rootPath.trim()) throw new Error("secure filesystem root must not be empty");
  const root = resolve(rootPath);
  if (root === parse(root).root) {
    throw new Error("refusing to use the filesystem root as a private data directory");
  }

  let rootStat = lstatOrNull(root);
  if (!rootStat) {
    if (!create) return null;
    // The caller owns the root's parent. The exact root is checked with lstat
    // immediately after creation so an existing or raced symlink is rejected.
    mkdirSync(root, { recursive: true, mode: 0o700 });
    rootStat = lstatOrNull(root);
  }
  if (!rootStat) throw new Error(`unable to create secure filesystem root: ${root}`);

  const identities = [directoryIdentity(root, rootStat)];
  const canonicalRoot = realpathSync.native(root);
  let current = root;
  let canonicalCurrent = canonicalRoot;

  for (const component of components) {
    validateComponent(component);
    current = resolve(current, component);
    if (!isContained(root, current)) {
      throw new Error(`filesystem path escapes private root: ${current}`);
    }
    let stat = lstatOrNull(current);
    if (!stat) {
      if (!create) return null;
      mkdirSync(current, { mode: 0o700 });
      stat = lstatOrNull(current);
    }
    if (!stat) throw new Error(`unable to create secure directory: ${current}`);
    identities.push(directoryIdentity(current, stat));
    canonicalCurrent = realpathSync.native(current);
    if (!isContained(canonicalRoot, canonicalCurrent)) {
      throw new Error(`canonical filesystem path escapes private root: ${current}`);
    }
  }

  return {
    root,
    canonicalRoot,
    path: current,
    canonicalPath: canonicalCurrent,
    identities,
  };
}

function assertDirectoryStable(directory: SecureDirectory): void {
  for (const identity of directory.identities) {
    const stat = lstatOrNull(identity.path);
    if (!stat || stat.isSymbolicLink() || !stat.isDirectory()) {
      throw new Error(`private directory changed during filesystem operation: ${identity.path}`);
    }
    if (stat.dev !== identity.dev || stat.ino !== identity.ino) {
      throw new Error(`private directory identity changed during filesystem operation: ${identity.path}`);
    }
  }
  const canonicalRoot = realpathSync.native(directory.root);
  const canonicalPath = realpathSync.native(directory.path);
  if (
    canonicalRoot !== directory.canonicalRoot
    || canonicalPath !== directory.canonicalPath
    || !isContained(canonicalRoot, canonicalPath, directory.path === directory.root)
  ) {
    throw new Error(`private directory canonical path changed: ${directory.path}`);
  }
}

function inspectRegularFile(path: string): Stats | null {
  const stat = lstatOrNull(path);
  if (!stat) return null;
  if (stat.isSymbolicLink()) {
    throw new Error(`refusing symbolic-link file: ${path}`);
  }
  if (!stat.isFile()) {
    throw new Error(`expected regular file: ${path}`);
  }
  if (stat.nlink !== 1) {
    throw new Error(`refusing multiply-linked private file: ${path}`);
  }
  return stat;
}

function sameIdentity(left: Stats | null, right: Stats | null): boolean {
  if (left === null || right === null) return left === right;
  return left.dev === right.dev && left.ino === right.ino;
}

function readBoundedDescriptor(fileDescriptor: number, maxBytes: number, path: string): string {
  const chunks: Buffer[] = [];
  let total = 0;
  while (true) {
    const chunk = Buffer.allocUnsafe(Math.min(READ_CHUNK_BYTES, maxBytes + 1 - total));
    const bytesRead = readSync(fileDescriptor, chunk, 0, chunk.length, null);
    if (bytesRead === 0) break;
    total += bytesRead;
    if (total > maxBytes) {
      throw new Error(`private file exceeds ${maxBytes} byte limit: ${path}`);
    }
    chunks.push(chunk.subarray(0, bytesRead));
  }
  return Buffer.concat(chunks, total).toString("utf-8");
}

function fsyncDirectory(path: string): void {
  let descriptor: number | undefined;
  try {
    descriptor = openSync(path, constants.O_RDONLY);
    fsyncSync(descriptor);
  } catch (error) {
    const code = errorCode(error);
    if (
      process.platform !== "win32"
      || (code !== "EINVAL" && code !== "ENOTSUP" && code !== "EPERM" && code !== "EISDIR")
    ) {
      throw error;
    }
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
  }
}

export function ensureSecureDirectory(root: string, components: readonly string[]): string {
  return resolveSecureDirectory(root, components, true)!.path;
}

export function listSecureDirectoryNames(
  root: string,
  components: readonly string[],
  maxEntries = 10_000,
): string[] {
  return readSecureDirectoryEntries(root, components, maxEntries)
    .filter((entry) => entry.isFile())
    .map((entry) => entry.name);
}

/** Count every directory entry, including subdirectories, under the same scan bound. */
export function countSecureDirectoryEntries(
  root: string,
  components: readonly string[],
  maxEntries = 10_000,
): number {
  return readSecureDirectoryEntries(root, components, maxEntries).length;
}

/** Check one validated entry name while retaining the directory scan bound. */
export function hasSecureDirectoryEntry(
  root: string,
  components: readonly string[],
  entryName: string,
  maxEntries = 10_000,
): boolean {
  validateComponent(entryName);
  return readSecureDirectoryEntries(root, components, maxEntries)
    .some((entry) => entry.name === entryName);
}

function readSecureDirectoryEntries(
  root: string,
  components: readonly string[],
  maxEntries: number,
): Dirent[] {
  const directory = resolveSecureDirectory(root, components, false);
  if (!directory) return [];
  assertDirectoryStable(directory);
  const entries = readdirSync(directory.path, { withFileTypes: true });
  if (entries.length > maxEntries) {
    throw new Error(`private directory exceeds ${maxEntries} entry limit: ${directory.path}`);
  }
  for (const entry of entries) {
    if (entry.isSymbolicLink()) {
      throw new Error(`refusing symbolic-link entry in private directory: ${entry.name}`);
    }
  }
  assertDirectoryStable(directory);
  return entries;
}

export function readSecureTextFile(
  root: string,
  components: readonly string[],
  filename: string,
  maxBytes: number,
): string | null {
  validateComponent(filename);
  const directory = resolveSecureDirectory(root, components, false);
  if (!directory) return null;
  assertDirectoryStable(directory);
  const path = resolve(directory.path, filename);
  const before = inspectRegularFile(path);
  if (!before) return null;
  if (before.size > maxBytes) {
    throw new Error(`private file exceeds ${maxBytes} byte limit: ${path}`);
  }

  const descriptor = openSync(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const opened = fstatSync(descriptor);
    if (!opened.isFile() || !sameIdentity(before, opened)) {
      throw new Error(`private file changed before secure open: ${path}`);
    }
    assertDirectoryStable(directory);
    return readBoundedDescriptor(descriptor, maxBytes, path);
  } finally {
    closeSync(descriptor);
  }
}

export function writeSecureTextFile(
  root: string,
  components: readonly string[],
  filename: string,
  content: string,
  options: SecureWriteOptions,
): string {
  validateComponent(filename);
  const contentBytes = Buffer.byteLength(content, "utf-8");
  if (contentBytes > options.maxBytes) {
    throw new Error(`private file exceeds ${options.maxBytes} byte limit: ${filename}`);
  }
  const directory = resolveSecureDirectory(root, components, true)!;
  assertDirectoryStable(directory);
  const destination = resolve(directory.path, filename);
  const before = inspectRegularFile(destination);
  if (before && options.replace === false) {
    throw new Error(`refusing to replace existing private file: ${destination}`);
  }

  const temporary = resolve(
    directory.path,
    `.${filename}.${process.pid}.${randomBytes(12).toString("hex")}.tmp`,
  );
  let descriptor: number | undefined;
  let temporaryIdentity: Stats | null = null;
  try {
    descriptor = openSync(
      temporary,
      constants.O_WRONLY
        | constants.O_CREAT
        | constants.O_EXCL
        | (constants.O_NOFOLLOW ?? 0),
      0o600,
    );
    temporaryIdentity = fstatSync(descriptor);
    if (!temporaryIdentity.isFile() || temporaryIdentity.nlink !== 1) {
      throw new Error(`unable to create isolated private temporary file: ${temporary}`);
    }
    writeFileSync(descriptor, content, { encoding: "utf-8" });
    fsyncSync(descriptor);
    closeSync(descriptor);
    descriptor = undefined;

    assertDirectoryStable(directory);
    const immediatelyBeforeRename = inspectRegularFile(destination);
    if (!sameIdentity(before, immediatelyBeforeRename)) {
      throw new Error(`private destination changed during atomic write: ${destination}`);
    }
    renameSync(temporary, destination);
    const written = inspectRegularFile(destination);
    if (!written || !sameIdentity(temporaryIdentity, written) || written.size !== contentBytes) {
      throw new Error(`atomic private write did not produce expected regular file: ${destination}`);
    }
    fsyncDirectory(directory.path);
    assertDirectoryStable(directory);
    return destination;
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
    const temporaryStat = lstatOrNull(temporary);
    if (
      temporaryStat
      && temporaryIdentity
      && !temporaryStat.isDirectory()
      && sameIdentity(temporaryStat, temporaryIdentity)
    ) {
      unlinkSync(temporary);
    }
  }
}

export function removeSecureFile(
  root: string,
  components: readonly string[],
  filename: string,
): boolean {
  validateComponent(filename);
  const directory = resolveSecureDirectory(root, components, false);
  if (!directory) return false;
  assertDirectoryStable(directory);
  const path = resolve(directory.path, filename);
  const before = inspectRegularFile(path);
  if (!before) return false;
  assertDirectoryStable(directory);
  const immediatelyBeforeUnlink = inspectRegularFile(path);
  if (!sameIdentity(before, immediatelyBeforeUnlink)) {
    throw new Error(`private file changed before removal: ${path}`);
  }
  unlinkSync(path);
  fsyncDirectory(directory.path);
  assertDirectoryStable(directory);
  return true;
}
