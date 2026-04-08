/** BlobStore factory (AC-518). */

import type { BlobStore } from "./store.js";
import { LocalBlobStore } from "./local.js";

export function createBlobStore(opts: {
  backend: string;
  root?: string;
  repoId?: string;
  cacheDir?: string;
}): BlobStore {
  if (opts.backend === "local") {
    return new LocalBlobStore(opts.root ?? "./blobs");
  }
  throw new Error(
    `Unknown blob store backend: ${opts.backend}. Available: 'local'`,
  );
}
