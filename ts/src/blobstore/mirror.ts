/** BlobMirror — hooks artifact writes into blob store (AC-518). */

import type { BlobRef } from "./ref.js";
import { createBlobRef } from "./ref.js";
import type { BlobStore } from "./store.js";
import type { BlobRegistry } from "./registry.js";

export class BlobMirror {
  constructor(
    private store: BlobStore,
    private minSizeBytes: number = 1024,
    private registry?: BlobRegistry,
  ) {}

  mirrorArtifact(
    key: string,
    data: Buffer,
    kind: string,
    runId?: string,
    artifactName?: string,
  ): BlobRef | null {
    if (data.length < this.minSizeBytes) return null;
    const digest = this.store.put(key, data);
    const ref = createBlobRef({
      kind,
      digest,
      sizeBytes: data.length,
      remoteUri: key,
    });
    if (this.registry && runId && artifactName)
      this.registry.register(runId, artifactName, ref);
    return ref;
  }

  mirrorFile(
    key: string,
    path: string,
    kind: string,
    sizeBytes: number,
    runId?: string,
    artifactName?: string,
  ): BlobRef | null {
    if (sizeBytes < this.minSizeBytes) return null;
    const digest = this.store.putFile(key, path);
    const ref = createBlobRef({
      kind,
      digest,
      sizeBytes,
      localPath: path,
      remoteUri: key,
    });
    if (this.registry && runId && artifactName)
      this.registry.register(runId, artifactName, ref);
    return ref;
  }
}
