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

import { ingestPublishedTraceFile, loadSeenTraceIds } from "./trace-ingest-workflow.js";
import {
  publishLocally,
  publishToGist,
  publishToHuggingFace,
} from "./publishing-workflow.js";
import type {
  IngestResult,
  PublishOpts,
  PublishResult,
  TraceArtifact,
} from "./publishers-types.js";

export type {
  IngestResult,
  PublishOpts,
  PublishResult,
  TraceArtifact,
} from "./publishers-types.js";

export class LocalPublisher {
  private outputDir: string;

  constructor(outputDir: string) {
    this.outputDir = outputDir;
  }

  async publish(artifact: TraceArtifact, _opts?: PublishOpts): Promise<PublishResult> {
    return publishLocally(this.outputDir, artifact);
  }
}

export class GistPublisher {
  private token: string;

  constructor(opts: { token: string }) {
    this.token = opts.token;
  }

  async publish(artifact: TraceArtifact, opts?: PublishOpts): Promise<PublishResult> {
    return publishToGist(this.token, artifact, opts);
  }
}

export class HuggingFacePublisher {
  private token: string;
  private repoId: string;

  constructor(opts: { token: string; repoId: string }) {
    this.token = opts.token;
    this.repoId = opts.repoId;
  }

  async publish(artifact: TraceArtifact, opts?: PublishOpts): Promise<PublishResult> {
    return publishToHuggingFace(this.token, this.repoId, artifact, opts);
  }
}

export class TraceIngester {
  private cacheDir: string;
  private seenIds: Set<string>;

  constructor(cacheDir: string) {
    this.cacheDir = cacheDir;
    this.seenIds = loadSeenTraceIds(cacheDir);
  }

  async ingestFromFile(filePath: string): Promise<IngestResult> {
    return ingestPublishedTraceFile({
      filePath,
      cacheDir: this.cacheDir,
      seenIds: this.seenIds,
    });
  }
}
