/** Deduplicated bucket-backed blob store (AC-518). */

export type { BlobStore, BlobStoreMeta } from "./store.js";
export type { BlobRef } from "./ref.js";
export { createBlobRef, isHydrated } from "./ref.js";
export { LocalBlobStore } from "./local.js";
export { BlobRegistry } from "./registry.js";
export { HydrationCache } from "./cache.js";
export { BlobMirror } from "./mirror.js";
export { SyncManager, type SyncResult } from "./sync.js";
export { createBlobStore } from "./factory.js";
