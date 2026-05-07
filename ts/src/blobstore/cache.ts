/** Hydration cache with digest verification (AC-518). */

import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { resolveBlobPath } from "./store.js";
import { fsError, isNotFoundError } from "./fs-errors.js";

export class HydrationCache {
  #maxBytes: number;
  #root: string;

  constructor(root: string, maxMb: number = 500) {
    this.#root = root;
    this.#maxBytes = maxMb * 1024 * 1024;
    try {
      mkdirSync(root, { recursive: true });
    } catch (error) {
      throw fsError("initialize cache", root, error);
    }
  }

  put(key: string, data: Buffer, _digest: string): void {
    const path = resolveBlobPath(this.#root, key);
    try {
      mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, data);
      this.#evictIfNeeded();
    } catch (error) {
      throw fsError("write cache entry", key, error);
    }
  }

  get(key: string, expectedDigest?: string): Buffer | null {
    const path = resolveBlobPath(this.#root, key);
    let data: Buffer;
    try {
      data = readFileSync(path);
    } catch (error) {
      if (isNotFoundError(error)) return null;
      throw fsError("read cache entry", key, error);
    }
    if (expectedDigest) {
      const actual =
        "sha256:" + createHash("sha256").update(data).digest("hex");
      if (actual !== expectedDigest) {
        try {
          unlinkSync(path);
        } catch (error) {
          if (!isNotFoundError(error)) {
            throw fsError("discard cache entry", key, error);
          }
        }
        return null;
      }
    }
    return data;
  }

  totalSizeBytes(): number {
    let total = 0;
    const walk = (dir: string): void => {
      if (!existsSync(dir)) return;
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
          continue;
        }
        total += statSync(full).size;
      }
    };
    try {
      walk(this.#root);
    } catch (error) {
      if (!isNotFoundError(error)) {
        throw fsError("scan cache", this.#root, error);
      }
    }
    return total;
  }

  clear(): void {
    const walk = (dir: string): void => {
      if (!existsSync(dir)) return;
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) walk(full);
        else unlinkSync(full);
      }
    };
    try {
      walk(this.#root);
    } catch (error) {
      if (!isNotFoundError(error)) {
        throw fsError("clear cache", this.#root, error);
      }
    }
  }

  #evictIfNeeded(): void {
    if (this.#maxBytes <= 0) return;
    let current = this.totalSizeBytes();
    if (current <= this.#maxBytes) return;
    const files: { path: string; mtime: number; size: number }[] = [];
    const walk = (dir: string): void => {
      if (!existsSync(dir)) return;
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
          continue;
        }
        const st = statSync(full);
        files.push({ path: full, mtime: st.mtimeMs, size: st.size });
      }
    };
    try {
      walk(this.#root);
    } catch (error) {
      if (!isNotFoundError(error)) {
        throw fsError("scan cache", this.#root, error);
      }
    }
    files.sort((a, b) => a.mtime - b.mtime);
    for (const f of files) {
      if (current <= this.#maxBytes) break;
      try {
        unlinkSync(f.path);
        current -= f.size;
      } catch (error) {
        if (!isNotFoundError(error)) {
          throw fsError("evict cache entry", f.path, error);
        }
      }
    }
  }
}
