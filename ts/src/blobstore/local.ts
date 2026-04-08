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

export class LocalBlobStore implements BlobStore {
  constructor(private root: string) {
    mkdirSync(root, { recursive: true });
  }

  put(key: string, data: Buffer): string {
    const path = resolveBlobPath(this.root, key);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, data);
    return sha256(data);
  }

  get(key: string): Buffer | null {
    const path = resolveBlobPath(this.root, key);
    if (!existsSync(path)) return null;
    return readFileSync(path);
  }

  head(key: string): BlobStoreMeta | null {
    const path = resolveBlobPath(this.root, key);
    if (!existsSync(path)) return null;
    const data = readFileSync(path);
    return {
      sizeBytes: data.length,
      digest: sha256(data),
      contentType: guessContentType(key),
    };
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
        const rel = relative(this.root, full).replace(/\\/g, "/");
        if (prefixMatches(rel, prefix)) results.push(rel);
      }
    };
    walk(this.root);
    return results.sort();
  }

  delete(key: string): boolean {
    const path = resolveBlobPath(this.root, key);
    if (!existsSync(path)) return false;
    unlinkSync(path);
    return true;
  }

  putFile(key: string, path: string): string {
    const dest = resolveBlobPath(this.root, key);
    mkdirSync(dirname(dest), { recursive: true });
    copyFileSync(path, dest);
    return sha256(readFileSync(dest));
  }

  getFile(key: string, dest: string): boolean {
    const src = resolveBlobPath(this.root, key);
    if (!existsSync(src)) return false;
    mkdirSync(dirname(dest), { recursive: true });
    copyFileSync(src, dest);
    return true;
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
