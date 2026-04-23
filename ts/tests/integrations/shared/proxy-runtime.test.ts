import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  buildProviderSourceInfo,
  finishInvocationTiming,
  resolveProviderIdentity,
  startInvocationClock,
} from "../../../src/integrations/_shared/proxy-runtime.js";
import {
  hashSessionId,
  hashUserId,
} from "../../../src/production-traces/sdk/hashing.js";

describe("provider proxy runtime", () => {
  let originalCwd: string;
  let dir: string;

  beforeEach(() => {
    originalCwd = process.cwd();
    dir = mkdtempSync(join(tmpdir(), "autoctx-provider-runtime-"));
    process.chdir(dir);
  });

  afterEach(() => {
    process.chdir(originalCwd);
    rmSync(dir, { recursive: true, force: true });
  });

  it("skips identity when no install salt exists", () => {
    expect(
      resolveProviderIdentity(
        { user_id: "user-123", session_id: "session-abc" },
        {},
      ),
    ).toEqual({});
  });

  it("hashes per-call identity with the install salt", () => {
    const salt = "a".repeat(64);
    mkdirSync(join(dir, ".autocontext"));
    writeFileSync(join(dir, ".autocontext", "install-salt"), `${salt}\n`, "utf-8");

    expect(
      resolveProviderIdentity(
        { user_id: "user-123", session_id: "session-abc" },
        {},
      ),
    ).toEqual({
      user_id_hash: hashUserId("user-123", salt),
      session_id_hash: hashSessionId("session-abc", salt),
    });
  });

  it("uses ambient identity when per-call identity is absent", () => {
    const salt = "b".repeat(64);
    mkdirSync(join(dir, ".autocontext"));
    writeFileSync(join(dir, ".autocontext", "install-salt"), `${salt}\n`, "utf-8");

    expect(resolveProviderIdentity(null, { userId: "ambient" })).toEqual({
      user_id_hash: hashUserId("ambient", salt),
    });
  });

  it("builds shared invocation timing envelopes", () => {
    const clock = startInvocationClock();
    const timing = finishInvocationTiming(clock);

    expect(timing.startedAt).toBe(clock.startedAt);
    expect(timing.endedAt).toMatch(/Z$/);
    expect(timing.latencyMs).toBeGreaterThanOrEqual(0);
  });

  it("builds provider source info from package metadata", () => {
    expect(buildProviderSourceInfo(import.meta.url).sdk.name).toBe("autocontext-ts");
  });
});
