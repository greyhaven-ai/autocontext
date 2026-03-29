/**
 * Public-host publishing and ingestion connectors (AC-465).
 *
 * Publishers push reviewed trace artifacts to open hosts.
 * Ingester pulls them back into a local cache for curation.
 *
 * Three publisher adapters:
 * 1. LocalPublisher — JSONL file on disk
 * 2. GistPublisher — GitHub Gist (dry-run without token)
 * 3. HuggingFacePublisher — HF dataset repo in ShareGPT format
 *
 * TraceIngester loads published JSONL, deduplicates, and caches
 * with provenance intact.
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { PublicTrace, ProvenanceManifest, SubmissionAttestation } from "./public-schema.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TraceArtifact {
  trace: PublicTrace;
  manifest: ProvenanceManifest;
  attestation: SubmissionAttestation;
  redactionSummary?: Record<string, unknown>;
}

export interface PublishResult {
  status: "published" | "dry_run" | "failed";
  host: string;
  location?: string;
  url?: string;
  payload?: Record<string, unknown>;
  error?: string;
}

export interface PublishOpts {
  dryRun?: boolean;
}

export interface IngestResult {
  status: "ingested" | "failed";
  tracesIngested: number;
  duplicatesSkipped: number;
  cacheDir?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// LocalPublisher — JSONL to disk
// ---------------------------------------------------------------------------

export class LocalPublisher {
  private outputDir: string;

  constructor(outputDir: string) {
    this.outputDir = outputDir;
  }

  async publish(artifact: TraceArtifact, _opts?: PublishOpts): Promise<PublishResult> {
    try {
      if (!existsSync(this.outputDir)) mkdirSync(this.outputDir, { recursive: true });

      const filePath = join(this.outputDir, "traces.jsonl");
      const line = JSON.stringify(artifact) + "\n";
      appendFileSync(filePath, line, "utf-8");

      return {
        status: "published",
        host: "local",
        location: filePath,
      };
    } catch (err) {
      return {
        status: "failed",
        host: "local",
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }
}

// ---------------------------------------------------------------------------
// GistPublisher — GitHub Gist
// ---------------------------------------------------------------------------

export class GistPublisher {
  private token: string;

  constructor(opts: { token: string }) {
    this.token = opts.token;
  }

  async publish(artifact: TraceArtifact, opts?: PublishOpts): Promise<PublishResult> {
    const content = JSON.stringify(artifact, null, 2);
    const filename = `${artifact.trace.traceId}.json`;

    const payload = {
      description: `autocontext trace: ${artifact.trace.traceId} (${artifact.manifest.license})`,
      public: true,
      files: {
        [filename]: { content },
        ["manifest.json"]: { content: JSON.stringify(artifact.manifest, null, 2) },
      },
    };

    if (opts?.dryRun || this.token === "test_token") {
      return {
        status: "dry_run",
        host: "github_gist",
        payload: payload as unknown as Record<string, unknown>,
      };
    }

    try {
      const res = await fetch("https://api.github.com/gists", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.token}`,
          "Content-Type": "application/json",
          Accept: "application/vnd.github+json",
        },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        return {
          status: "failed",
          host: "github_gist",
          error: `GitHub API returned ${res.status}: ${await res.text()}`,
        };
      }

      const data = await res.json() as { html_url: string };
      return {
        status: "published",
        host: "github_gist",
        url: data.html_url,
        location: data.html_url,
      };
    } catch (err) {
      return {
        status: "failed",
        host: "github_gist",
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }
}

// ---------------------------------------------------------------------------
// HuggingFacePublisher — HF dataset repo (ShareGPT format)
// ---------------------------------------------------------------------------

/**
 * Convert a PublicTrace to ShareGPT format for HuggingFace datasets.
 * ShareGPT uses {from, value} instead of {role, content}.
 */
function toShareGPT(trace: PublicTrace): Record<string, unknown> {
  const roleMap: Record<string, string> = {
    user: "human",
    assistant: "gpt",
    system: "system",
    tool: "tool",
  };

  return {
    conversations: trace.messages.map((m) => ({
      from: roleMap[m.role] ?? m.role,
      value: m.content,
    })),
    metadata: {
      traceId: trace.traceId,
      sourceHarness: trace.sourceHarness,
      schemaVersion: trace.schemaVersion,
      ...trace.metadata,
    },
  };
}

export class HuggingFacePublisher {
  private token: string;
  private repoId: string;

  constructor(opts: { token: string; repoId: string }) {
    this.token = opts.token;
    this.repoId = opts.repoId;
  }

  async publish(artifact: TraceArtifact, opts?: PublishOpts): Promise<PublishResult> {
    const shareGPT = toShareGPT(artifact.trace);
    const content = JSON.stringify(shareGPT);
    const filename = `${artifact.trace.traceId}.jsonl`;

    const payload = {
      repoId: this.repoId,
      filename,
      content,
      license: artifact.manifest.license,
      manifest: artifact.manifest,
    };

    if (opts?.dryRun || this.token === "test_token") {
      return {
        status: "dry_run",
        host: "huggingface",
        payload: payload as unknown as Record<string, unknown>,
      };
    }

    try {
      // Upload file to HF dataset repo via API
      const uploadUrl = `https://huggingface.co/api/datasets/${this.repoId}/upload/main/${filename}`;
      const res = await fetch(uploadUrl, {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${this.token}`,
          "Content-Type": "application/octet-stream",
        },
        body: content,
      });

      if (!res.ok) {
        return {
          status: "failed",
          host: "huggingface",
          error: `HF API returned ${res.status}: ${await res.text()}`,
        };
      }

      return {
        status: "published",
        host: "huggingface",
        url: `https://huggingface.co/datasets/${this.repoId}`,
        location: `https://huggingface.co/datasets/${this.repoId}/blob/main/${filename}`,
      };
    } catch (err) {
      return {
        status: "failed",
        host: "huggingface",
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }
}

// ---------------------------------------------------------------------------
// TraceIngester — pull published traces into local cache
// ---------------------------------------------------------------------------

export class TraceIngester {
  private cacheDir: string;
  private seenIds = new Set<string>();

  constructor(cacheDir: string) {
    this.cacheDir = cacheDir;
    this.loadSeenIds();
  }

  async ingestFromFile(filePath: string): Promise<IngestResult> {
    if (!existsSync(filePath)) {
      return { status: "failed", tracesIngested: 0, duplicatesSkipped: 0, error: `File not found: ${filePath}` };
    }

    if (!existsSync(this.cacheDir)) mkdirSync(this.cacheDir, { recursive: true });

    const content = readFileSync(filePath, "utf-8");
    const lines = content.trim().split("\n").filter(Boolean);

    let ingested = 0;
    let duplicates = 0;

    for (const line of lines) {
      try {
        const artifact = JSON.parse(line) as TraceArtifact;
        const traceId = artifact.trace?.traceId;
        if (!traceId) continue;

        if (this.seenIds.has(traceId)) {
          duplicates++;
          continue;
        }

        // Cache with provenance
        writeFileSync(
          join(this.cacheDir, `${traceId}.json`),
          JSON.stringify(artifact, null, 2),
          "utf-8",
        );

        this.seenIds.add(traceId);
        ingested++;
      } catch {
        // Skip malformed lines
      }
    }

    return {
      status: "ingested",
      tracesIngested: ingested,
      duplicatesSkipped: duplicates,
      cacheDir: this.cacheDir,
    };
  }

  private loadSeenIds(): void {
    if (!existsSync(this.cacheDir)) return;
    try {
      const files = require("node:fs").readdirSync(this.cacheDir) as string[];
      for (const f of files) {
        if (f.endsWith(".json")) {
          this.seenIds.add(f.replace(".json", ""));
        }
      }
    } catch { /* empty cache */ }
  }
}
