/** BlobStore abstract interface (AC-518). */

export interface BlobStoreMeta {
  sizeBytes: number;
  digest: string;
  contentType: string;
}

export interface BlobStore {
  put(key: string, data: Buffer): string;
  get(key: string): Buffer | null;
  head(key: string): BlobStoreMeta | null;
  listPrefix(prefix: string): string[];
  delete(key: string): boolean;
  putFile(key: string, path: string): string;
  getFile(key: string, dest: string): boolean;
}
