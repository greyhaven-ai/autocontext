/**
 * A2-I Layer 2 — file extension → language mapping.
 *
 * Spec §5.1 item 4: supported extensions + their language tags.
 */
import { describe, test, expect } from "vitest";
import {
  languageFromPath,
  isSupportedPath,
} from "../../../../src/control-plane/instrument/scanner/file-type-filter.js";

describe("languageFromPath", () => {
  test("Python extensions → 'python'", () => {
    expect(languageFromPath("src/app.py")).toBe("python");
    expect(languageFromPath("deeply/nested/dir/module.py")).toBe("python");
  });

  test("TypeScript extensions → 'typescript'", () => {
    expect(languageFromPath("src/app.ts")).toBe("typescript");
    expect(languageFromPath("src/app.mts")).toBe("typescript");
    expect(languageFromPath("src/app.cts")).toBe("typescript");
  });

  test("TSX extension → 'tsx'", () => {
    expect(languageFromPath("src/App.tsx")).toBe("tsx");
  });

  test("JavaScript extensions → 'javascript'", () => {
    expect(languageFromPath("src/app.js")).toBe("javascript");
    expect(languageFromPath("src/app.mjs")).toBe("javascript");
    expect(languageFromPath("src/app.cjs")).toBe("javascript");
  });

  test("JSX extension → 'jsx'", () => {
    expect(languageFromPath("src/App.jsx")).toBe("jsx");
  });

  test("unsupported extensions → null", () => {
    expect(languageFromPath("README.md")).toBe(null);
    expect(languageFromPath("image.png")).toBe(null);
    expect(languageFromPath("config.json")).toBe(null);
    expect(languageFromPath("script.sh")).toBe(null);
    expect(languageFromPath("style.css")).toBe(null);
    expect(languageFromPath("binary.so")).toBe(null);
  });

  test("dotfiles without extensions → null", () => {
    expect(languageFromPath(".env")).toBe(null);
    expect(languageFromPath(".gitignore")).toBe(null);
    expect(languageFromPath("src/.env.local")).toBe(null);
  });

  test("case-insensitive extension matching", () => {
    expect(languageFromPath("src/App.TS")).toBe("typescript");
    expect(languageFromPath("src/App.TSX")).toBe("tsx");
  });

  test("multi-dot filenames resolve to final extension", () => {
    expect(languageFromPath("types.d.ts")).toBe("typescript");
    expect(languageFromPath("server.config.ts")).toBe("typescript");
  });

  test("Windows separator handled", () => {
    expect(languageFromPath("src\\app.py")).toBe("python");
  });

  test("isSupportedPath mirrors languageFromPath", () => {
    expect(isSupportedPath("src/app.py")).toBe(true);
    expect(isSupportedPath("README.md")).toBe(false);
  });
});
