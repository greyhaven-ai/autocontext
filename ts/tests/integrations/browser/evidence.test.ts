import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, expect, test } from "vitest";

import { BrowserEvidenceStore } from "../../../src/integrations/browser/evidence.js";

describe("browser evidence store", () => {
  test("appendAuditEvent writes JSONL", () => {
    const rootDir = mkdtempSync(join(tmpdir(), "browser-evidence-"));
    const store = new BrowserEvidenceStore({ rootDir });

    const path = store.appendAuditEvent({
      schemaVersion: "1.0",
      eventId: "evt_1",
      sessionId: "session_1",
      actionId: "act_1",
      kind: "action_result",
      allowed: true,
      policyReason: "allowed",
      timestamp: "2026-04-22T12:00:02Z",
      message: "navigation allowed",
      beforeUrl: "about:blank",
      afterUrl: "https://example.com",
      artifacts: {
        htmlPath: null,
        screenshotPath: null,
        downloadPath: null,
      },
    });

    const lines = readFileSync(path, "utf-8").trim().split("\n");
    expect(lines).toHaveLength(1);
    expect(JSON.parse(lines[0]).eventId).toBe("evt_1");
  });

  test("persistSnapshotArtifacts writes html and png", () => {
    const rootDir = mkdtempSync(join(tmpdir(), "browser-evidence-"));
    const store = new BrowserEvidenceStore({ rootDir });

    const result = store.persistSnapshotArtifacts({
      sessionId: "session_1",
      basename: "snap_1",
      html: "<html><body>Hello</body></html>",
      screenshotBase64: Buffer.from("png-bytes").toString("base64"),
    });

    expect(result.htmlPath).toBeTruthy();
    expect(result.screenshotPath).toBeTruthy();
    expect(readFileSync(result.htmlPath!, "utf-8")).toBe("<html><body>Hello</body></html>");
    expect(readFileSync(result.screenshotPath!)).toEqual(Buffer.from("png-bytes"));
  });

  test("persistSnapshotArtifacts sanitizes traversal inputs", () => {
    const rootDir = mkdtempSync(join(tmpdir(), "browser-evidence-"));
    const store = new BrowserEvidenceStore({ rootDir });

    const result = store.persistSnapshotArtifacts({
      sessionId: "../session_1",
      basename: "../../../../../escaped",
      html: "<html><body>Hello</body></html>",
      screenshotBase64: Buffer.from("png-bytes").toString("base64"),
    });

    expect(resolve(result.htmlPath!)).toMatch(new RegExp(`^${resolve(rootDir)}`));
    expect(resolve(result.screenshotPath!)).toMatch(new RegExp(`^${resolve(rootDir)}`));
    expect(result.htmlPath).toContain("/escaped.html");
    expect(result.screenshotPath).toContain("/escaped.png");
  });
});
