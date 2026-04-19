import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  loadRedactionPolicy,
  saveRedactionPolicy,
  defaultRedactionPolicy,
  redactionPolicyPath,
} from "../../../../src/production-traces/redaction/policy.js";

describe("redaction policy", () => {
  let cwd: string;

  beforeEach(() => {
    cwd = mkdtempSync(join(tmpdir(), "autocontext-redaction-policy-"));
  });

  afterEach(() => {
    rmSync(cwd, { recursive: true, force: true });
  });

  test("defaultRedactionPolicy returns on-export with standard auto-detect categories and blanket rawProviderPayload", () => {
    const p = defaultRedactionPolicy();
    expect(p.schemaVersion).toBe("1.0");
    expect(p.mode).toBe("on-export");
    expect(p.autoDetect.enabled).toBe(true);
    expect(p.autoDetect.categories).toEqual([
      "pii-email",
      "pii-phone",
      "pii-ssn",
      "pii-credit-card",
      "secret-token",
    ]);
    expect(p.customPatterns).toEqual([]);
    expect(p.rawProviderPayload.behavior).toBe("blanket-mark");
    expect(p.exportPolicy.placeholder).toBe("[redacted]");
    expect(p.exportPolicy.preserveLength).toBe(false);
    expect(p.exportPolicy.includeRawProviderPayload).toBe(false);
    expect(p.exportPolicy.includeMetadata).toBe(true);
    expect(p.exportPolicy.categoryOverrides).toEqual({});
  });

  test("loadRedactionPolicy returns defaults when policy file does not exist", async () => {
    const p = await loadRedactionPolicy(cwd);
    expect(p).toEqual(defaultRedactionPolicy());
  });

  test("saveRedactionPolicy then loadRedactionPolicy round-trips unchanged", async () => {
    const source = {
      ...defaultRedactionPolicy(),
      mode: "on-ingest" as const,
      customPatterns: [
        {
          name: "internal-ticket-id",
          regex: "TICKET-\\d{6,}",
          category: "pii-custom",
          reason: "pii-custom" as const,
        },
      ],
      exportPolicy: {
        ...defaultRedactionPolicy().exportPolicy,
        categoryOverrides: {
          "pii-email": { action: "hash" as const, hashSalt: "install-specific" },
        },
      },
    };

    await saveRedactionPolicy(cwd, source);
    const loaded = await loadRedactionPolicy(cwd);

    expect(loaded).toEqual(source);
  });

  test("saveRedactionPolicy writes a canonical JSON file with stable key order", async () => {
    const p = defaultRedactionPolicy();
    await saveRedactionPolicy(cwd, p);

    const content = readFileSync(redactionPolicyPath(cwd), "utf-8");
    // Canonical: sorted keys — "autoDetect" < "customPatterns" < "exportPolicy" < "mode" < "rawProviderPayload" < "schemaVersion"
    // Not newline-delimited JCS, but we do sort keys so byte-output is stable.
    const autoDetectIdx = content.indexOf("\"autoDetect\"");
    const modeIdx = content.indexOf("\"mode\"");
    const schemaVersionIdx = content.indexOf("\"schemaVersion\"");
    expect(autoDetectIdx).toBeGreaterThan(-1);
    expect(autoDetectIdx).toBeLessThan(modeIdx);
    expect(modeIdx).toBeLessThan(schemaVersionIdx);
  });

  test("loadRedactionPolicy rejects malformed policy with clear error", async () => {
    mkdirSync(join(cwd, ".autocontext", "production-traces"), { recursive: true });
    writeFileSync(redactionPolicyPath(cwd), JSON.stringify({ bogus: true }));

    await expect(loadRedactionPolicy(cwd)).rejects.toThrow(/redaction-policy/i);
  });

  test("loadRedactionPolicy rejects policy with invalid mode", async () => {
    const bad = {
      ...defaultRedactionPolicy(),
      mode: "never" as unknown as "on-export",
    };
    mkdirSync(join(cwd, ".autocontext", "production-traces"), { recursive: true });
    writeFileSync(redactionPolicyPath(cwd), JSON.stringify(bad));

    await expect(loadRedactionPolicy(cwd)).rejects.toThrow(/redaction-policy/i);
  });

  test("loadRedactionPolicy rejects policy with invalid category override action", async () => {
    const p = defaultRedactionPolicy();
    const bad = {
      ...p,
      exportPolicy: {
        ...p.exportPolicy,
        categoryOverrides: {
          "pii-email": { action: "mutate" },
        },
      },
    };
    mkdirSync(join(cwd, ".autocontext", "production-traces"), { recursive: true });
    writeFileSync(redactionPolicyPath(cwd), JSON.stringify(bad));

    await expect(loadRedactionPolicy(cwd)).rejects.toThrow(/redaction-policy/i);
  });

  test("redactionPolicyPath is under .autocontext/production-traces/", () => {
    const p = redactionPolicyPath(cwd);
    expect(p.endsWith(join(".autocontext", "production-traces", "redaction-policy.json"))).toBe(true);
  });

  test("saveRedactionPolicy creates parent directory if missing", async () => {
    // cwd exists but nested dirs do not.
    await saveRedactionPolicy(cwd, defaultRedactionPolicy());
    expect(existsSync(redactionPolicyPath(cwd))).toBe(true);
  });
});
