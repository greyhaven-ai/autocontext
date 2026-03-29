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
import { join, relative, extname } from "node:path";

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
  /** Lines/entries that couldn't be parsed (not silently dropped) */
  warnings: string[];
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
const DATA_EXTENSIONS = new Set([".jsonl", ".json", ".csv"]);
const IGNORE_FILES = new Set(["package.json", "tsconfig.json", "package-lock.json", ".autoctx-data.json"]);

export class DatasetDiscovery {
  scan(repoRoot: string): DiscoveredDataset[] {
    const results: DiscoveredDataset[] = [];

    // 1. Check manifest
    const manifestPath = join(repoRoot, ".autoctx-data.json");
    if (existsSync(manifestPath)) {
      try {
        const manifest = JSON.parse(readFileSync(manifestPath, "utf-8")) as DiscoveryManifest;
        for (const entry of manifest.datasets ?? []) {
          const absPath = join(repoRoot, entry.path);
          if (existsSync(absPath)) {
            results.push({
              absolutePath: absPath,
              relativePath: entry.path,
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
      const dirPath = join(repoRoot, dir);
      if (!existsSync(dirPath)) continue;
      try {
        if (!statSync(dirPath).isDirectory()) continue;
      } catch { continue; }

      this.scanDirectory(dirPath, repoRoot, results, manifestPaths);
    }

    return results;
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
      return { status: "failed", records: [], provenance, warnings: [], error: `File not found: ${dataset.absolutePath}` };
    }

    try {
      let records: ShareGPTRecord[];
      const warnings: string[] = [];
      switch (dataset.format) {
        case "jsonl":
          records = this.adaptJSONL(dataset.absolutePath, warnings);
          break;
        case "json":
          records = this.adaptJSON(dataset.absolutePath);
          break;
        case "csv":
          records = this.adaptCSV(dataset.absolutePath);
          break;
        default:
          return { status: "failed", records: [], provenance, warnings: [], error: `Unsupported format: ${dataset.format}` };
      }
      return { status: "adapted", records, provenance, warnings };
    } catch (err) {
      return { status: "failed", records: [], provenance, warnings: [], error: err instanceof Error ? err.message : String(err) };
    }
  }

  private adaptJSONL(path: string, warnings: string[] = []): ShareGPTRecord[] {
    const content = readFileSync(path, "utf-8");
    const records: ShareGPTRecord[] = [];
    const lines = content.trim().split("\n");
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (!line.trim()) continue;
      try {
        const parsed = JSON.parse(line) as Record<string, unknown>;
        if (Array.isArray(parsed.conversations)) {
          records.push(parsed as unknown as ShareGPTRecord);
        } else if (parsed.input && parsed.output) {
          records.push(this.ioPairToShareGPT(parsed));
        }
      } catch (err) {
        warnings.push(`Line ${i + 1}: ${err instanceof Error ? err.message : "parse error"}`);
      }
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
    let i = 0;
    while (i < line.length) {
      const char = line[i];
      if (char === '"') {
        if (inQuotes && i + 1 < line.length && line[i + 1] === '"') {
          // Escaped quote: "" → literal "
          current += '"';
          i += 2;
          continue;
        }
        inQuotes = !inQuotes;
      } else if (char === "," && !inQuotes) {
        values.push(current);
        current = "";
      } else {
        current += char;
      }
      i++;
    }
    values.push(current);
    return values;
  }
}
