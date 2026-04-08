/** SyncManager — bulk sync local runs to blob store (AC-518). */

import { existsSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import type { BlobStore } from "./store.js";

export interface SyncResult {
  runId: string;
  syncedCount: number;
  skippedCount: number;
  totalBytes: number;
  errors: string[];
}

export class SyncManager {
  constructor(
    private store: BlobStore,
    private runsRoot: string,
  ) {}

  syncRun(runId: string): SyncResult {
    const runDir = join(this.runsRoot, runId);
    if (!existsSync(runDir))
      return {
        runId,
        syncedCount: 0,
        skippedCount: 0,
        totalBytes: 0,
        errors: [],
      };

    let synced = 0,
      skipped = 0,
      totalBytes = 0;
    const errors: string[] = [];

    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
          continue;
        }
        const key = `runs/${runId}/${relative(runDir, full)}`;
        try {
          if (this.store.head(key)) {
            skipped++;
            continue;
          }
          this.store.putFile(key, full);
          synced++;
          totalBytes += statSync(full).size;
        } catch (e) {
          errors.push(`${key}: ${e}`);
        }
      }
    };
    walk(runDir);
    return {
      runId,
      syncedCount: synced,
      skippedCount: skipped,
      totalBytes,
      errors,
    };
  }

  status(): {
    totalBlobs: number;
    totalBytes: number;
    syncedRuns: string[];
    runCount: number;
  } {
    const keys = this.store.listPrefix("runs/");
    let totalBytes = 0;
    const runs = new Set<string>();
    for (const key of keys) {
      const parts = key.split("/");
      if (parts.length >= 2) runs.add(parts[1]);
      const meta = this.store.head(key);
      if (meta) totalBytes += meta.sizeBytes;
    }
    return {
      totalBlobs: keys.length,
      totalBytes,
      syncedRuns: [...runs].sort(),
      runCount: runs.size,
    };
  }
}
