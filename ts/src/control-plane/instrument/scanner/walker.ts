/**
 * A2-I scanner — repo walker.
 *
 * Spec §5.1: DFS, alphabetical-within-directory for determinism. Per file,
 * filter chain applied in order:
 *
 *   1. Hardcoded defaults (canonical list lives in safety/hardcoded-defaults.ts;
 *      Layer 3 moved the constant there — scanner imports from safety per
 *      spec §3.3's allowed scanner→safety direction for constants/primitives)
 *   2. .gitignore patterns (nested cascade via `ignore` npm package — remains
 *      inline here; see safety/index.ts for the non-extraction rationale)
 *   3. Extra excludes from --exclude + --exclude-from (gitignore syntax)
 *   4. Extension filter (.py/.ts/.tsx/.mts/.cts/.js/.jsx/.mjs/.cjs)
 *   5. File-size cap (default 1 MB; over-cap files logged + skipped)
 *
 * Surviving files yielded as SourceFile instances.
 */
import { readdir, readFile, stat } from "node:fs/promises";
import { join, relative, sep, posix } from "node:path";
import ignore from "ignore";
import type { SourceFile } from "../contract/plugin-interface.js";
import { HARDCODED_DEFAULT_PATTERNS } from "../safety/hardcoded-defaults.js";
import { fromBytes } from "./source-file.js";
import { languageFromPath } from "./file-type-filter.js";

const DEFAULT_MAX_FILE_BYTES = 1_048_576;

export interface ScanOpts {
  readonly cwd: string;
  readonly extraExcludes?: readonly string[];
  readonly excludeFrom?: string;
  readonly maxFileBytes?: number;
  /** Optional override for deterministic testing. Defaults to console.warn. */
  readonly onSkipOversized?: (path: string, sizeBytes: number) => void;
}

/** Async-iterable repo walk. Yields `SourceFile` instances in deterministic order. */
export async function* scanRepo(opts: ScanOpts): AsyncIterable<SourceFile> {
  const cwd = opts.cwd;
  const maxBytes = opts.maxFileBytes ?? DEFAULT_MAX_FILE_BYTES;

  // Hardcoded defaults always apply. Sourced from safety/hardcoded-defaults.ts.
  const defaultsIgnore = ignore().add([...HARDCODED_DEFAULT_PATTERNS]);

  // Extra excludes layered on top — user-supplied via --exclude and --exclude-from.
  const extraIgnore = ignore();
  if (opts.extraExcludes && opts.extraExcludes.length > 0) {
    extraIgnore.add([...opts.extraExcludes]);
  }
  if (opts.excludeFrom) {
    const txt = await readFile(opts.excludeFrom, "utf-8");
    extraIgnore.add(txt);
  }

  // Emit absolute paths in deterministic order first, then stat + read.
  for await (const abs of walkDirDFS(cwd, cwd, defaultsIgnore, extraIgnore, [])) {
    const relPath = toPosix(relative(cwd, abs));
    if (languageFromPath(relPath) === null) continue;

    const st = await stat(abs);
    if (st.size > maxBytes) {
      (opts.onSkipOversized ?? defaultOversizedLogger)(relPath, st.size);
      continue;
    }

    const bytes = await readFile(abs);
    const language = languageFromPath(relPath)!;
    yield fromBytes({ path: relPath, language, bytes });
  }
}

/**
 * DFS walk. `gitignoreStack` is the cumulative list of gitignore pattern lines
 * collected from `cwd` down to `absDir`. When descending into a subdirectory,
 * we re-check its `.gitignore` and push its lines onto a fresh copy of the stack;
 * this avoids leaking sibling branches' patterns into sibling subtrees.
 */
async function* walkDirDFS(
  absDir: string,
  repoRoot: string,
  defaultsIgnore: ReturnType<typeof ignore>,
  extraIgnore: ReturnType<typeof ignore>,
  gitignoreStack: readonly string[],
): AsyncIterable<string> {
  const entries = await readdir(absDir, { withFileTypes: true });
  // Deterministic order: alphabetical by name (case-sensitive per POSIX).
  entries.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));

  // If this directory has its own .gitignore, extend the stack.
  let dirStack = gitignoreStack;
  const gi = entries.find((e) => e.isFile() && e.name === ".gitignore");
  if (gi) {
    const txt = await readFile(join(absDir, ".gitignore"), "utf-8");
    dirStack = gitignoreStack.concat(splitNonEmptyLines(txt));
  }
  const dirIgnore = ignore().add(dirStack as string[]);

  for (const e of entries) {
    const absPath = join(absDir, e.name);
    const relFromRoot = toPosix(relative(repoRoot, absPath));
    if (relFromRoot.length === 0) continue;

    if (e.isDirectory()) {
      const dirMarker = relFromRoot + "/";
      if (defaultsIgnore.ignores(dirMarker) || defaultsIgnore.ignores(relFromRoot)) continue;
      if (dirIgnore.ignores(dirMarker) || dirIgnore.ignores(relFromRoot)) continue;
      if (extraIgnore.ignores(dirMarker) || extraIgnore.ignores(relFromRoot)) continue;
      yield* walkDirDFS(absPath, repoRoot, defaultsIgnore, extraIgnore, dirStack);
    } else if (e.isFile()) {
      if (defaultsIgnore.ignores(relFromRoot)) continue;
      if (dirIgnore.ignores(relFromRoot)) continue;
      if (extraIgnore.ignores(relFromRoot)) continue;
      yield absPath;
    }
  }
}

function defaultOversizedLogger(path: string, sizeBytes: number): void {
  // eslint-disable-next-line no-console
  console.warn(`[autoctx instrument] skipped oversized file: ${path} (${sizeBytes} bytes)`);
}

function splitNonEmptyLines(txt: string): string[] {
  return txt
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l.length > 0 && !l.startsWith("#"));
}

function toPosix(p: string): string {
  if (sep === posix.sep) return p;
  return p.split(sep).join(posix.sep);
}
