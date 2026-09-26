import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { configureSqliteDatabase } from "../src/storage/sqlite-store.js";

describe("storage database workflow", () => {
  it("owns sqlite pragma setup in sqlite-store instead of a wrapper helper module", () => {
    const storageDir = join(import.meta.dirname, "..", "src", "storage");
    const sqliteStoreSource = readFileSync(join(storageDir, "sqlite-store.ts"), "utf-8");

    expect(sqliteStoreSource).not.toContain("./storage-database-workflow.js");
    expect(existsSync(join(storageDir, "storage-database-workflow.ts"))).toBe(false);
  });

  it("applies sqlite pragmas in the expected order", () => {
    const pragma = vi.fn((source: string) =>
      source.startsWith("journal_mode") ? "wal" : undefined,
    );
    configureSqliteDatabase({ pragma } as never);
    expect(pragma).toHaveBeenNthCalledWith(1, "journal_mode = WAL", { simple: true });
    expect(pragma).toHaveBeenNthCalledWith(2, "foreign_keys = ON");
  });
});
