import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { SessionContext } from "./session.js";
import {
  hashSessionId,
  hashUserId,
  installSaltPath,
} from "../../production-traces/sdk/hashing.js";

export interface InvocationClock {
  startedAt: string;
  startedMonotonic: number;
}

export interface InvocationTiming {
  startedAt: string;
  endedAt: string;
  latencyMs: number;
}

export interface ProviderSourceInfo {
  emitter: string;
  sdk: { name: string; version: string };
}

export function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function startInvocationClock(): InvocationClock {
  return { startedAt: nowIso(), startedMonotonic: Date.now() };
}

export function finishInvocationTiming(clock: InvocationClock): InvocationTiming {
  return {
    startedAt: clock.startedAt,
    endedAt: nowIso(),
    latencyMs: Date.now() - clock.startedMonotonic,
  };
}

export function resolveProviderIdentity(
  perCall: Record<string, string> | null | undefined,
  ambient: SessionContext,
): Record<string, string> {
  const raw: Record<string, string> = {};
  if (perCall) {
    if (perCall["user_id"] != null) raw["user_id"] = perCall["user_id"];
    if (perCall["session_id"] != null) raw["session_id"] = perCall["session_id"];
  }
  if (Object.keys(raw).length === 0) {
    if (ambient.userId) raw["user_id"] = ambient.userId;
    if (ambient.sessionId) raw["session_id"] = ambient.sessionId;
  }
  if (Object.keys(raw).length === 0) return {};

  const salt = loadInstallSaltSync(".");
  if (!salt) return {};

  const hashed: Record<string, string> = {};
  if (raw["user_id"]) hashed["user_id_hash"] = hashUserId(raw["user_id"], salt);
  if (raw["session_id"]) {
    hashed["session_id_hash"] = hashSessionId(raw["session_id"], salt);
  }
  return hashed;
}

export function loadInstallSaltSync(cwd: string): string | null {
  try {
    const saltPath = installSaltPath(cwd);
    if (!existsSync(saltPath)) return null;
    const content = readFileSync(saltPath, "utf-8").trim();
    return content || null;
  } catch {
    return null;
  }
}

let cachedPackageVersion: string | null = null;

export function resolvePackageVersion(importMetaUrl: string): string {
  if (cachedPackageVersion !== null) return cachedPackageVersion;
  try {
    let dir = dirname(fileURLToPath(importMetaUrl));
    for (let depth = 0; depth < 10; depth++) {
      const candidate = join(dir, "package.json");
      if (existsSync(candidate)) {
        const pkg = JSON.parse(readFileSync(candidate, "utf-8")) as {
          name?: string;
          version?: string;
        };
        if (pkg.name === "autoctx" && typeof pkg.version === "string") {
          cachedPackageVersion = pkg.version;
          return cachedPackageVersion;
        }
      }
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  } catch {
    // best-effort
  }
  cachedPackageVersion = "0.0.0";
  return cachedPackageVersion;
}

export function buildProviderSourceInfo(importMetaUrl: string): ProviderSourceInfo {
  return {
    emitter: "sdk",
    sdk: { name: "autocontext-ts", version: resolvePackageVersion(importMetaUrl) },
  };
}
