import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";

import type { PublicTrace } from "./public-schema.js";
import type { PublishOpts, PublishResult, TraceArtifact } from "./publishers-types.js";

export function toShareGPTTrace(trace: PublicTrace): Record<string, unknown> {
  const roleMap: Record<string, string> = {
    user: "human",
    assistant: "gpt",
    system: "system",
    tool: "tool",
  };

  return {
    conversations: trace.messages.map((message) => ({
      from: roleMap[message.role] ?? message.role,
      value: message.content,
    })),
    metadata: {
      traceId: trace.traceId,
      sourceHarness: trace.sourceHarness,
      schemaVersion: trace.schemaVersion,
      ...trace.metadata,
    },
  };
}

export function toPublishedDatasetRow(artifact: TraceArtifact): Record<string, unknown> {
  return {
    ...toShareGPTTrace(artifact.trace),
    provenance: artifact.manifest,
    attestation: artifact.attestation,
    redactionSummary: artifact.redactionSummary,
  };
}

export function buildGistPayload(artifact: TraceArtifact): Record<string, unknown> {
  const filename = `${artifact.trace.traceId}.json`;
  return {
    description: `autocontext trace: ${artifact.trace.traceId} (${artifact.manifest.license})`,
    public: true,
    files: {
      [filename]: { content: JSON.stringify(artifact, null, 2) },
      ["manifest.json"]: { content: JSON.stringify(artifact.manifest, null, 2) },
    },
  };
}

export function buildHuggingFacePayload(
  artifact: TraceArtifact,
  repoId: string,
): Record<string, unknown> {
  const filename = `${artifact.trace.traceId}.json`;
  return {
    repoId,
    filename,
    content: JSON.stringify(toPublishedDatasetRow(artifact)),
    license: artifact.manifest.license,
    manifest: artifact.manifest,
  };
}

export function publishLocally(
  outputDir: string,
  artifact: TraceArtifact,
): PublishResult {
  try {
    if (!existsSync(outputDir)) {
      mkdirSync(outputDir, { recursive: true });
    }

    const filePath = join(outputDir, "traces.jsonl");
    appendFileSync(filePath, `${JSON.stringify(artifact)}\n`, "utf-8");
    return { status: "published", host: "local", location: filePath };
  } catch (err) {
    return {
      status: "failed",
      host: "local",
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

export async function publishToGist(
  token: string,
  artifact: TraceArtifact,
  opts?: PublishOpts,
): Promise<PublishResult> {
  const payload = buildGistPayload(artifact);

  if (opts?.dryRun || token === "test_token") {
    return { status: "dry_run", host: "github_gist", payload };
  }

  try {
    const response = await fetch("https://api.github.com/gists", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        Accept: "application/vnd.github+json",
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      return {
        status: "failed",
        host: "github_gist",
        error: `GitHub API returned ${response.status}: ${await response.text()}`,
      };
    }

    const data = await response.json() as { html_url: string };
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

export async function publishToHuggingFace(
  token: string,
  repoId: string,
  artifact: TraceArtifact,
  opts?: PublishOpts,
): Promise<PublishResult> {
  const payload = buildHuggingFacePayload(artifact, repoId);

  if (opts?.dryRun || token === "test_token") {
    return { status: "dry_run", host: "huggingface", payload };
  }

  try {
    const uploadUrl = `https://huggingface.co/api/datasets/${repoId}/upload/main/${payload.filename as string}`;
    const response = await fetch(uploadUrl, {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/octet-stream",
      },
      body: payload.content as string,
    });

    if (!response.ok) {
      return {
        status: "failed",
        host: "huggingface",
        error: `HF API returned ${response.status}: ${await response.text()}`,
      };
    }

    return {
      status: "published",
      host: "huggingface",
      url: `https://huggingface.co/datasets/${repoId}`,
      location: `https://huggingface.co/datasets/${repoId}/blob/main/${payload.filename as string}`,
    };
  } catch (err) {
    return {
      status: "failed",
      host: "huggingface",
      error: err instanceof Error ? err.message : String(err),
    };
  }
}
