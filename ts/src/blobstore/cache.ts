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

export class HydrationCache {
  private maxBytes: number;
  constructor(
    private root: string,
    maxMb: number = 500,
  ) {
    this.maxBytes = maxMb * 1024 * 1024;
    mkdirSync(root, { recursive: true });
  }

  put(key: string, data: Buffer, _digest: string): void {
    const path = join(this.root, key);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, data);
    this.evictIfNeeded();
  }

  get(key: string, expectedDigest?: string): Buffer | null {
    const path = join(this.root, key);
    if (!existsSync(path)) return null;
    const data = readFileSync(path);
    if (expectedDigest) {
      const actual =
        "sha256:" + createHash("sha256").update(data).digest("hex");
      if (actual !== expectedDigest) {
        unlinkSync(path);
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
    walk(this.root);
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
    walk(this.root);
  }

  private evictIfNeeded(): void {
    if (this.maxBytes <= 0) return;
    let current = this.totalSizeBytes();
    if (current <= this.maxBytes) return;
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
    walk(this.root);
    files.sort((a, b) => a.mtime - b.mtime);
    for (const f of files) {
      if (current <= this.maxBytes) break;
      unlinkSync(f.path);
      current -= f.size;
    }
  }
}
