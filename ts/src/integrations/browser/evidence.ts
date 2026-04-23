import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { canonicalJsonStringify } from "../../control-plane/contract/canonical-json.js";
import type { BrowserAuditEvent } from "./contract/index.js";
import { validateBrowserAuditEvent } from "./contract/index.js";

export interface BrowserArtifactPaths {
  readonly htmlPath: string | null;
  readonly screenshotPath: string | null;
  readonly downloadPath: string | null;
}

export interface BrowserEvidenceStoreOpts {
  readonly rootDir: string;
}

export interface PersistSnapshotArtifactsOpts {
  readonly sessionId: string;
  readonly basename: string;
  readonly html?: string | null;
  readonly screenshotBase64?: string | null;
}

export class BrowserEvidenceStore {
  readonly rootDir: string;

  constructor(opts: BrowserEvidenceStoreOpts) {
    this.rootDir = resolve(opts.rootDir);
  }

  appendAuditEvent(event: BrowserAuditEvent): string {
    assertValidAuditEvent(event);
    const sessionDir = this.sessionDir(event.sessionId);
    mkdirSync(sessionDir, { recursive: true });
    const outPath = join(sessionDir, "actions.jsonl");
    appendFileSync(outPath, canonicalJsonStringify(event) + "\n", "utf-8");
    return outPath;
  }

  persistSnapshotArtifacts(opts: PersistSnapshotArtifactsOpts): BrowserArtifactPaths {
    const sessionDir = this.sessionDir(opts.sessionId);
    let htmlPath: string | null = null;
    let screenshotPath: string | null = null;

    if (opts.html !== undefined && opts.html !== null) {
      htmlPath = join(sessionDir, "html", `${opts.basename}.html`);
      mkdirSync(join(sessionDir, "html"), { recursive: true });
      writeFileSync(htmlPath, opts.html, "utf-8");
    }

    if (opts.screenshotBase64 !== undefined && opts.screenshotBase64 !== null) {
      screenshotPath = join(sessionDir, "screenshots", `${opts.basename}.png`);
      mkdirSync(join(sessionDir, "screenshots"), { recursive: true });
      writeFileSync(screenshotPath, Buffer.from(opts.screenshotBase64, "base64"));
    }

    return {
      htmlPath,
      screenshotPath,
      downloadPath: null,
    };
  }

  private sessionDir(sessionId: string): string {
    return join(this.rootDir, "browser", "sessions", sessionId);
  }
}

function assertValidAuditEvent(event: BrowserAuditEvent): void {
  const validation = validateBrowserAuditEvent(event);
  if (!validation.valid) {
    throw new TypeError(`invalid browser audit event: ${validation.errors.join("; ")}`);
  }
}
