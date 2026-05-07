/** Local filesystem blob store — content-addressed by SHA256 (AC-518). */

import { createHash } from "node:crypto";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, relative } from "node:path";
import {
  type BlobStore,
  type BlobStoreMeta,
  prefixMatches,
  resolveBlobPath,
} from "./store.js";
import { fsError, isNotFoundError } from "./fs-errors.js";

export class LocalBlobStore implements BlobStore {
  #root: string;

  constructor(root: string) {
    this.#root = root;
    try {
      mkdirSync(root, { recursive: true });
    } catch (error) {
      throw fsError("initialize blob store", root, error);
    }
  }

  put(key: string, data: Buffer): string {
    const path = resolveBlobPath(this.#root, key);
    try {
      mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, data);
      return sha256(data);
    } catch (error) {
      throw fsError("write blob", key, error);
    }
  }

  get(key: string): Buffer | null {
    const path = resolveBlobPath(this.#root, key);
    try {
      return readFileSync(path);
    } catch (error) {
      if (isNotFoundError(error)) return null;
      throw fsError("read blob", key, error);
    }
  }

  head(key: string): BlobStoreMeta | null {
    const path = resolveBlobPath(this.#root, key);
    try {
      const data = readFileSync(path);
      return {
        sizeBytes: data.length,
        digest: sha256(data),
        contentType: guessContentType(key),
      };
    } catch (error) {
      if (isNotFoundError(error)) return null;
      throw fsError("read blob metadata", key, error);
    }
  }

  listPrefix(prefix: string): string[] {
    const results: string[] = [];
    const walk = (dir: string): void => {
      if (!existsSync(dir)) return;
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
          continue;
        }
        const rel = relative(this.#root, full).replace(/\\/g, "/");
        if (prefixMatches(rel, prefix)) results.push(rel);
      }
    };
    try {
      walk(this.#root);
    } catch (error) {
      if (isNotFoundError(error)) return [];
      throw fsError("list blobs for prefix", prefix, error);
    }
    return results.sort();
  }

  delete(key: string): boolean {
    const path = resolveBlobPath(this.#root, key);
    try {
      unlinkSync(path);
      return true;
    } catch (error) {
      if (isNotFoundError(error)) return false;
      throw fsError("delete blob", key, error);
    }
  }

  putFile(key: string, path: string): string {
    const dest = resolveBlobPath(this.#root, key);
    try {
      mkdirSync(dirname(dest), { recursive: true });
      copyFileSync(path, dest);
      return sha256(readFileSync(dest));
    } catch (error) {
      throw fsError("write blob from file", key, error);
    }
  }

  getFile(key: string, dest: string): boolean {
    const src = resolveBlobPath(this.#root, key);
    try {
      mkdirSync(dirname(dest), { recursive: true });
      copyFileSync(src, dest);
      return true;
    } catch (error) {
      if (isNotFoundError(error)) return false;
      throw fsError("write blob to file", key, error);
    }
  }
}

function sha256(data: Buffer): string {
  return "sha256:" + createHash("sha256").update(data).digest("hex");
}

function guessContentType(key: string): string {
  if (key.endsWith(".json")) return "application/json";
  if (key.endsWith(".ndjson")) return "application/x-ndjson";
  if (key.endsWith(".md")) return "text/markdown";
  if (key.endsWith(".txt")) return "text/plain";
  return "application/octet-stream";
}
