/**
 * Repo-local dataset discovery and schema adaptation (AC-461).
 *
 * DatasetDiscovery scans a repo tree for candidate training data:
 * - Conventional directories (data/, fixtures/, benchmarks/, examples/)
 * - Manifest files (.autoctx-data.json)
 * - File format detection (JSONL, JSON, CSV)
 *
 * DatasetAdapter converts discovered files into ShareGPT training format
 * with full provenance tracking.
 */

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { extname, isAbsolute, join, relative, resolve } from "node:path";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface DiscoveredDataset {
  absolutePath: string;
  relativePath: string;
  format: "jsonl" | "json" | "csv" | "markdown" | "unknown";
  source: "manifest" | "conventional_dir" | "file_scan";
  scenario?: string;
}

export interface ShareGPTRecord {
  conversations: Array<{ from: string; value: string }>;
  metadata?: Record<string, unknown>;
}

export interface DatasetProvenance {
  sourcePath: string;
  sourceFormat: string;
  scenario?: string;
  adaptedAt: string;
  transformationMethod: string;
}

export interface AdaptedDataset {
  status: "adapted" | "failed";
  records: ShareGPTRecord[];
  provenance: DatasetProvenance;
  error?: string;
}

export interface DiscoveryManifest {
  datasets: Array<{
    path: string;
    format?: string;
    scenario?: string;
  }>;
}

// ---------------------------------------------------------------------------
// Discovery
// ---------------------------------------------------------------------------

const CONVENTIONAL_DIRS = ["data", "fixtures", "benchmarks", "examples", "training_data", "datasets"];
const DATA_EXTENSIONS = new Set([".jsonl", ".json", ".csv", ".md"]);
const IGNORE_FILES = new Set(["package.json", "tsconfig.json", "package-lock.json", ".autoctx-data.json"]);

export class DatasetDiscovery {
  scan(repoRoot: string): DiscoveredDataset[] {
    const resolvedRoot = resolve(repoRoot);
    const results: DiscoveredDataset[] = [];

    // 1. Check manifest
    const manifestPath = join(resolvedRoot, ".autoctx-data.json");
    if (existsSync(manifestPath)) {
      try {
        const manifest = JSON.parse(readFileSync(manifestPath, "utf-8")) as DiscoveryManifest;
        for (const entry of manifest.datasets ?? []) {
          const absPath = this.resolveRepoLocalPath(resolvedRoot, entry.path);
          if (!absPath) continue;
          if (existsSync(absPath)) {
            results.push({
              absolutePath: absPath,
              relativePath: relative(resolvedRoot, absPath),
              format: this.detectFormat(entry.path, entry.format),
              source: "manifest",
              scenario: entry.scenario,
            });
          }
        }
      } catch { /* malformed manifest */ }
    }

    // 2. Scan conventional directories
    const manifestPaths = new Set(results.map((r) => r.absolutePath));
    for (const dir of CONVENTIONAL_DIRS) {
      const dirPath = join(resolvedRoot, dir);
      if (!existsSync(dirPath)) continue;
      try {
        if (!statSync(dirPath).isDirectory()) continue;
      } catch { continue; }

      this.scanDirectory(dirPath, resolvedRoot, results, manifestPaths);
    }

    return results;
  }

  private resolveRepoLocalPath(repoRoot: string, candidatePath: string): string | null {
    const absolutePath = resolve(repoRoot, candidatePath);
    const repoRelative = relative(repoRoot, absolutePath);
    if (repoRelative === "" || (!repoRelative.startsWith("..") && !isAbsolute(repoRelative))) {
      return absolutePath;
    }
    return null;
  }

  private scanDirectory(
    dirPath: string,
    repoRoot: string,
    results: DiscoveredDataset[],
    skipPaths: Set<string>,
  ): void {
    let entries: string[];
    try {
      entries = readdirSync(dirPath);
    } catch { return; }

    for (const entry of entries) {
      const absPath = join(dirPath, entry);
      if (skipPaths.has(absPath)) continue;

      try {
        const stat = statSync(absPath);
        if (stat.isDirectory()) {
          this.scanDirectory(absPath, repoRoot, results, skipPaths);
          continue;
        }
        if (!stat.isFile()) continue;
      } catch { continue; }

      const ext = extname(entry).toLowerCase();
      if (!DATA_EXTENSIONS.has(ext)) continue;
      if (IGNORE_FILES.has(entry)) continue;

      const relPath = relative(repoRoot, absPath);
      results.push({
        absolutePath: absPath,
        relativePath: relPath,
        format: this.detectFormat(relPath),
        source: "conventional_dir",
      });
    }
  }

  private detectFormat(path: string, hint?: string): DiscoveredDataset["format"] {
    if (hint) {
      if (hint.includes("jsonl") || hint.includes("sharegpt")) return "jsonl";
      if (hint.includes("json")) return "json";
      if (hint.includes("csv")) return "csv";
      if (hint.includes("markdown") || hint.includes("md")) return "markdown";
    }
    const ext = extname(path).toLowerCase();
    switch (ext) {
      case ".jsonl": return "jsonl";
      case ".json": return "json";
      case ".csv": return "csv";
      case ".md": return "markdown";
      default: return "unknown";
    }
  }
}

// ---------------------------------------------------------------------------
// Adapter
// ---------------------------------------------------------------------------

export class DatasetAdapter {
  adapt(dataset: DiscoveredDataset): AdaptedDataset {
    const provenance: DatasetProvenance = {
      sourcePath: dataset.relativePath,
      sourceFormat: dataset.format,
      scenario: dataset.scenario,
      adaptedAt: new Date().toISOString(),
      transformationMethod: `adapt_${dataset.format}`,
    };

    if (!existsSync(dataset.absolutePath)) {
      return { status: "failed", records: [], provenance, error: `File not found: ${dataset.absolutePath}` };
    }

    try {
      switch (dataset.format) {
        case "jsonl":
          return { status: "adapted", records: this.adaptJSONL(dataset.absolutePath), provenance };
        case "json":
          return { status: "adapted", records: this.adaptJSON(dataset.absolutePath), provenance };
        case "csv":
          return { status: "adapted", records: this.adaptCSV(dataset.absolutePath), provenance };
        case "markdown":
          return { status: "adapted", records: this.adaptMarkdown(dataset.absolutePath), provenance };
        default:
          return { status: "failed", records: [], provenance, error: `Unsupported format: ${dataset.format}` };
      }
    } catch (err) {
      return { status: "failed", records: [], provenance, error: err instanceof Error ? err.message : String(err) };
    }
  }

  private adaptJSONL(path: string): ShareGPTRecord[] {
    const content = readFileSync(path, "utf-8");
    const records: ShareGPTRecord[] = [];
    for (const line of content.trim().split("\n")) {
      if (!line.trim()) continue;
      try {
        const parsed = JSON.parse(line) as Record<string, unknown>;
        if (Array.isArray(parsed.conversations)) {
          // Already ShareGPT format
          records.push(parsed as unknown as ShareGPTRecord);
        } else if (parsed.input && parsed.output) {
          records.push(this.ioPairToShareGPT(parsed));
        }
      } catch { /* skip malformed lines */ }
    }
    return records;
  }

  private adaptJSON(path: string): ShareGPTRecord[] {
    const content = readFileSync(path, "utf-8");
    const parsed = JSON.parse(content);

    if (Array.isArray(parsed)) {
      return parsed
        .filter((item) => item && typeof item === "object")
        .map((item) => {
          if (item.conversations) return item as ShareGPTRecord;
          if (item.input != null || item.prompt != null) return this.ioPairToShareGPT(item);
          return null;
        })
        .filter(Boolean) as ShareGPTRecord[];
    }

    if (parsed.conversations) return [parsed as ShareGPTRecord];
    if (parsed.input || parsed.prompt) return [this.ioPairToShareGPT(parsed)];
    return [];
  }

  private adaptCSV(path: string): ShareGPTRecord[] {
    const content = readFileSync(path, "utf-8");
    const lines = content.trim().split("\n");
    if (lines.length < 2) return [];

    const headers = this.parseCSVLine(lines[0]).map((h) => h.toLowerCase().trim());
    const promptCol = headers.findIndex((h) => h === "prompt" || h === "input" || h === "question");
    const responseCol = headers.findIndex((h) => h === "response" || h === "output" || h === "answer");

    if (promptCol < 0 || responseCol < 0) return [];

    const records: ShareGPTRecord[] = [];
    for (let i = 1; i < lines.length; i++) {
      const values = this.parseCSVLine(lines[i]);
      if (values.length <= Math.max(promptCol, responseCol)) continue;
      records.push({
        conversations: [
          { from: "human", value: values[promptCol].trim() },
          { from: "gpt", value: values[responseCol].trim() },
        ],
      });
    }
    return records;
  }

  private adaptMarkdown(path: string): ShareGPTRecord[] {
    const content = readFileSync(path, "utf-8");
    const sections = this.parseMarkdownSections(content);
    const prompt = this.findMarkdownSection(sections, [
      "input",
      "prompt",
      "question",
      "task",
      "instruction",
    ]);
    const response = this.findMarkdownSection(sections, [
      "expected output",
      "output",
      "response",
      "answer",
      "solution",
    ]);

    if (!prompt || !response) {
      return [];
    }

    return [{
      conversations: [
        { from: "human", value: prompt },
        { from: "gpt", value: response },
      ],
    }];
  }

  private ioPairToShareGPT(item: Record<string, unknown>): ShareGPTRecord {
    const prompt = String(item.input ?? item.prompt ?? item.question ?? "");
    const response = String(item.output ?? item.response ?? item.answer ?? "");
    return {
      conversations: [
        { from: "human", value: prompt },
        { from: "gpt", value: response },
      ],
      metadata: item.score != null ? { score: item.score } : undefined,
    };
  }

  private parseCSVLine(line: string): string[] {
    const values: string[] = [];
    let current = "";
    let inQuotes = false;
    for (const char of line) {
      if (char === '"') {
        inQuotes = !inQuotes;
      } else if (char === "," && !inQuotes) {
        values.push(current);
        current = "";
      } else {
        current += char;
      }
    }
    values.push(current);
    return values;
  }

  private parseMarkdownSections(content: string): Map<string, string> {
    const sections = new Map<string, string>();
    const lines = content.split(/\r?\n/);
    let currentHeading: string | null = null;
    let buffer: string[] = [];

    const flush = () => {
      if (!currentHeading) return;
      const sectionBody = buffer.join("\n").trim();
      if (sectionBody) {
        sections.set(currentHeading, sectionBody);
      }
    };

    for (const line of lines) {
      const match = /^(#{1,6})\s+(.+?)\s*$/.exec(line);
      if (match) {
        flush();
        currentHeading = this.normalizeHeading(match[2]);
        buffer = [];
        continue;
      }
      if (currentHeading) {
        buffer.push(line);
      }
    }
    flush();

    return sections;
  }

  private findMarkdownSection(sections: Map<string, string>, candidates: string[]): string | undefined {
    for (const candidate of candidates) {
      const normalizedCandidate = this.normalizeHeading(candidate);
      for (const [heading, body] of sections.entries()) {
        if (
          heading === normalizedCandidate
          || heading.includes(normalizedCandidate)
          || normalizedCandidate.includes(heading)
        ) {
          return body;
        }
      }
    }
    return undefined;
  }

  private normalizeHeading(heading: string): string {
    return heading
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }
}
