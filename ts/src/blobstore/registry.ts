/** BlobRegistry — tracks BlobRefs by run + artifact name (AC-518). */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import type { BlobRef } from "./ref.js";
import { createBlobRef } from "./ref.js";

export class BlobRegistry {
  private entries: Map<string, Map<string, BlobRef>> = new Map();

  register(runId: string, name: string, ref: BlobRef): void {
    if (!this.entries.has(runId)) this.entries.set(runId, new Map());
    this.entries.get(runId)!.set(name, ref);
  }

  lookup(runId: string, name: string): BlobRef | null {
    return this.entries.get(runId)?.get(name) ?? null;
  }

  listForRun(runId: string): BlobRef[] {
    return [...(this.entries.get(runId)?.values() ?? [])];
  }

  save(path: string): void {
    const data: Record<string, Record<string, BlobRef>> = {};
    for (const [runId, map] of this.entries) {
      data[runId] = Object.fromEntries(map);
    }
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, JSON.stringify(data, null, 2), "utf-8");
  }

  static load(path: string): BlobRegistry {
    const registry = new BlobRegistry();
    if (!existsSync(path)) return registry;
    const data = JSON.parse(readFileSync(path, "utf-8")) as Record<
      string,
      Record<string, BlobRef>
    >;
    for (const [runId, entries] of Object.entries(data)) {
      for (const [name, refData] of Object.entries(entries)) {
        registry.register(
          runId,
          name,
          createBlobRef(
            refData as Partial<BlobRef> & {
              kind: string;
              digest: string;
              sizeBytes: number;
            },
          ),
        );
      }
    }
    return registry;
  }
}
