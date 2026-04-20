/**
 * A2-I Layer 2 — tree-sitter loader: lazy grammar loading, parser caching,
 * cross-language isolation.
 *
 * Spec §5.2: grammars load lazily; `loadParser(language)` is cached per language;
 * grammars for unused languages never load.
 */
import { describe, test, expect, beforeEach } from "vitest";
import {
  loadParser,
  parseSource,
  loadedGrammarsSnapshot,
  __resetForTests,
  type TreeSitterTree,
} from "../../../../src/control-plane/instrument/scanner/tree-sitter-loader.js";

beforeEach(() => {
  __resetForTests();
});

describe("tree-sitter loader", () => {
  test("loadParser caches per-language (same instance returned on repeated calls)", async () => {
    const p1 = await loadParser("python");
    const p2 = await loadParser("python");
    expect(p1).toBe(p2);
    expect(p1.language).toBe("python");
  });

  test("only requested grammars are loaded (lazy)", async () => {
    expect(loadedGrammarsSnapshot().size).toBe(0);
    await loadParser("python");
    expect(loadedGrammarsSnapshot().has("python")).toBe(true);
    expect(loadedGrammarsSnapshot().has("typescript")).toBe(false);
    expect(loadedGrammarsSnapshot().has("javascript")).toBe(false);
  });

  test("parseSource on Python produces a tree with a root node", async () => {
    const tree = await parseSource("python", "x = 1\n");
    expect(tree).toBeDefined();
    expect((tree as TreeSitterTree).rootNode).toBeDefined();
  });

  test("parseSource on TypeScript produces a tree", async () => {
    const tree = await parseSource("typescript", "const x: number = 1;\n");
    expect((tree as TreeSitterTree).rootNode).toBeDefined();
  });

  test("parseSource on JavaScript produces a tree", async () => {
    const tree = await parseSource("javascript", "const x = 1;\n");
    expect((tree as TreeSitterTree).rootNode).toBeDefined();
  });

  test("parseSource on TSX produces a tree (different grammar instance from typescript)", async () => {
    const tsParser = await loadParser("typescript");
    const tsxParser = await loadParser("tsx");
    // Different language keys, different cache entries.
    expect(tsParser.language).toBe("typescript");
    expect(tsxParser.language).toBe("tsx");
    expect(tsParser).not.toBe(tsxParser);

    const tree = await parseSource("tsx", "const x = <Hello />\n");
    expect((tree as TreeSitterTree).rootNode).toBeDefined();
  });

  test("parser.parse called more than once does not corrupt cache", async () => {
    await parseSource("python", "x = 1\n");
    const tree = await parseSource("python", "y = 2\n");
    expect((tree as TreeSitterTree).rootNode).toBeDefined();
  });
});
