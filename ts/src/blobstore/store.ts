/** BlobStore abstract interface (AC-518). */

import { isAbsolute, relative, resolve } from "node:path";

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

export function normalizeBlobKey(
  key: string,
  opts: { allowEmpty?: boolean } = {},
): string {
  if (!key) {
    if (opts.allowEmpty) return "";
    throw new Error("blob key must not be empty");
  }

  if (isAbsolute(key) || /^[a-zA-Z]:[\\/]/.test(key)) {
    throw new Error(`invalid blob key: ${JSON.stringify(key)}`);
  }

  const normalized = key.replace(/\\/g, "/");
  const parts = normalized
    .split("/")
    .filter((part) => part !== "" && part !== ".");
  if (parts.some((part) => part === "..")) {
    throw new Error(`invalid blob key: ${JSON.stringify(key)}`);
  }

  const joined = parts.join("/");
  if (!joined && !opts.allowEmpty) {
    throw new Error("blob key must not be empty");
  }
  return joined;
}

export function resolveBlobPath(root: string, key: string): string {
  const normalized = normalizeBlobKey(key);
  const rootResolved = resolve(root);
  const candidate = resolve(rootResolved, normalized);
  const rel = relative(rootResolved, candidate);
  if (rel === ".." || rel.startsWith(`..${"/"}`) || rel.startsWith(`..${"\\"}`) || isAbsolute(rel)) {
    throw new Error(`invalid blob key: ${JSON.stringify(key)}`);
  }
  return candidate;
}

export function prefixMatches(key: string, prefix: string): boolean {
  const normalizedPrefix = normalizeBlobKey(prefix, { allowEmpty: true });
  if (!normalizedPrefix) return true;
  if (prefix.endsWith("/") || prefix.endsWith("\\")) {
    return key === normalizedPrefix || key.startsWith(`${normalizedPrefix}/`);
  }
  return key.startsWith(normalizedPrefix);
}
