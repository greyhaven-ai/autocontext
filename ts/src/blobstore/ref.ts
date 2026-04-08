/** BlobRef — structured artifact locator (AC-518). */

import { existsSync } from "node:fs";

export interface BlobRef {
  kind: string;
  digest: string;
  sizeBytes: number;
  localPath: string;
  remoteUri: string;
  contentType: string;
  createdAt: string;
  retentionClass: string;
}

export function createBlobRef(
  opts: Partial<BlobRef> & { kind: string; digest: string; sizeBytes: number },
): BlobRef {
  return {
    kind: opts.kind,
    digest: opts.digest,
    sizeBytes: opts.sizeBytes,
    localPath: opts.localPath ?? "",
    remoteUri: opts.remoteUri ?? "",
    contentType: opts.contentType ?? "",
    createdAt: opts.createdAt ?? "",
    retentionClass: opts.retentionClass ?? "",
  };
}

export function isHydrated(ref: BlobRef): boolean {
  return ref.localPath !== "" && existsSync(ref.localPath);
}
