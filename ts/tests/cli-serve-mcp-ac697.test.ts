/**
 * AC-697 slice 6: TS `autoctx serve mcp` canonical path.
 *
 * Slice 1 (PR #981) pinned the canonical contract: `serve mcp` is
 * the canonical path for the MCP server; `mcp-serve` stays as a
 * top-level alias. This slice adds sub-arg dispatch in `cmdServeHttp`:
 * when invoked as `autoctx serve mcp`, it rewrites argv and routes
 * to the existing `cmdMcpServe` handler, so the MCP server logic
 * stays single-sourced.
 *
 * Same delegation pattern as slice 4's `cmdScenario` -> `cmdNewScenario`.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "vitest";

import { visibleSupportedCommandNames } from "../src/cli/command-registry.js";
import {
  MCP_SERVE_HELP_TEXT,
  SERVE_MCP_HELP_TEXT,
  buildMcpServeHelpText,
} from "../src/cli/mcp-serve-command-workflow.js";
import { SERVE_HELP_TEXT } from "../src/cli/serve-command-workflow.js";

describe("AC-697 slice 6: TS `serve mcp` is canonical, `mcp-serve` is alias", () => {
  test("contract: TS `serve.mcp` is `yes`", () => {
    const path = resolve(import.meta.dirname, "..", "..", "docs", "cli-contract.json");
    const contract = JSON.parse(readFileSync(path, "utf-8")) as {
      commands: { id: string; runtime_support: { typescript: { status: string } } }[];
    };
    const serveMcp = contract.commands.find((c) => c.id === "serve.mcp");
    expect(serveMcp).toBeDefined();
    expect(serveMcp!.runtime_support.typescript.status).toBe("yes");
  });

  test("contract: `mcp-serve` is preserved as the slice-1 alias", () => {
    const path = resolve(import.meta.dirname, "..", "..", "docs", "cli-contract.json");
    const contract = JSON.parse(readFileSync(path, "utf-8")) as {
      commands: { id: string; aliases: string[] }[];
    };
    const serveMcp = contract.commands.find((c) => c.id === "serve.mcp");
    expect(serveMcp).toBeDefined();
    expect(serveMcp!.aliases).toContain("mcp-serve");
  });

  test("`serve` and `mcp-serve` are both registered in command-registry", () => {
    const registered = new Set(visibleSupportedCommandNames());
    // Canonical path: `serve` is the top-level command; `serve mcp` is
    // sub-arg dispatch inside cmdServeHttp.
    expect(registered.has("serve")).toBe(true);
    // Legacy alias kept for backward compat with existing Claude
    // Code MCP configurations.
    expect(registered.has("mcp-serve")).toBe(true);
  });

  // PR #1001 review (P3): the canonical help text must reflect the
  // canonical command name. The slice-6 delegation routed
  // `serve mcp --help` through cmdMcpServe, which printed the
  // legacy `autoctx mcp-serve` header. The builder now takes the
  // command name and the two surfaces stay byte-identical except
  // for the header. Same pattern as PR #999's scenario fix.
  test("buildMcpServeHelpText renders the body once with a configurable command-name header", () => {
    const legacyBody = MCP_SERVE_HELP_TEXT.split("\n").slice(1).join("\n");
    const canonicalBody = SERVE_MCP_HELP_TEXT.split("\n").slice(1).join("\n");
    expect(canonicalBody).toBe(legacyBody);
  });

  test("legacy MCP help header still names `mcp-serve`", () => {
    expect(MCP_SERVE_HELP_TEXT.split("\n")[0]).toBe(
      "autoctx mcp-serve — Start MCP server on stdio",
    );
  });

  test("canonical MCP help header names `serve mcp`", () => {
    expect(SERVE_MCP_HELP_TEXT.split("\n")[0]).toBe(
      "autoctx serve mcp — Start MCP server on stdio",
    );
  });

  test("buildMcpServeHelpText is the single source of truth for both surfaces", () => {
    expect(buildMcpServeHelpText("mcp-serve")).toBe(MCP_SERVE_HELP_TEXT);
    expect(buildMcpServeHelpText("serve mcp")).toBe(SERVE_MCP_HELP_TEXT);
  });

  test("`autoctx serve --help` mentions the new `serve mcp` subcommand", () => {
    // Before slice 6 the help text described only the HTTP server,
    // so the canonical MCP path was invisible to operators reading
    // `--help`. The expanded help text now lists `mcp` as a
    // subcommand alongside the canonical-path note.
    expect(SERVE_HELP_TEXT).toMatch(/mcp/);
    expect(SERVE_HELP_TEXT.toLowerCase()).toContain("subcommand");
  });
});
