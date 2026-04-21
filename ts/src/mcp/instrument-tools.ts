// MCP tool for the `autoctx instrument` command (A2-I).
//
// Thin wrapper around the in-process `runInstrumentCommand` runner. Keeps the
// CLI and MCP paths aligned (same convention as Foundation A's
// production-traces-tools and Foundation B's core-control-tools).
//
// The single tool `instrument` accepts the same flags as the CLI + an optional
// `mode` parameter; returns the raw CliResult JSON (`{stdout, stderr, exitCode}`)
// so the agent integrator can parse stdout or inspect advisory stderr.

import { z } from "zod";
import { runInstrumentCommand } from "../control-plane/instrument/cli/index.js";

interface JsonToolResponse {
  content: Array<{ type: "text"; text: string }>;
}

type McpToolRegistrar = {
  tool: (...args: any[]) => unknown;
};

function jsonText(payload: unknown, indent = 2): JsonToolResponse {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(payload, null, indent),
      },
    ],
  };
}

export function registerInstrumentTools(server: McpToolRegistrar): void {
  server.tool(
    "instrument",
    "Scan a repo for LLM clients and propose/apply Autocontext wrappers. " +
      "In A2-I the plugin registry ships empty; A2-II+ will register SDK-specific " +
      "DetectorPlugins. Returns {stdout, stderr, exitCode}.",
    {
      cwd: z.string().optional(),
      mode: z.enum(["dry-run", "apply", "apply-branch"]).optional(),
      branch: z.string().optional(),
      commit: z.string().optional(),
      exclude: z.array(z.string()).optional(),
      excludeFrom: z.string().optional(),
      maxFileBytes: z.number().int().positive().optional(),
      failIfEmpty: z.boolean().optional(),
      force: z.boolean().optional(),
      enhanced: z.boolean().optional(),
    },
    async (args: Record<string, unknown>) => {
      const argv: string[] = [];
      const mode = (args.mode as string | undefined) ?? "dry-run";
      if (mode === "apply" || mode === "apply-branch") argv.push("--apply");
      if (mode === "apply-branch") {
        if (typeof args.branch === "string" && args.branch.length > 0) {
          argv.push("--branch", args.branch);
        } else {
          // apply-branch requires a branch name — leave the runner to reject.
          argv.push("--branch", "autocontext-instrument");
        }
      }
      if (mode !== "apply-branch" && mode !== "apply") argv.push("--dry-run");
      if (typeof args.commit === "string" && (mode === "apply" || mode === "apply-branch")) {
        argv.push("--commit", args.commit);
      }
      const excludes = Array.isArray(args.exclude) ? (args.exclude as string[]) : [];
      for (const g of excludes) argv.push("--exclude", g);
      if (typeof args.excludeFrom === "string") argv.push("--exclude-from", args.excludeFrom);
      if (typeof args.maxFileBytes === "number") argv.push("--max-file-bytes", String(args.maxFileBytes));
      if (args.failIfEmpty === true) argv.push("--fail-if-empty");
      if (args.force === true) argv.push("--force");
      if (args.enhanced === true) argv.push("--enhanced");
      argv.push("--output", "json");

      const result = await runInstrumentCommand(argv, args.cwd ? { cwd: args.cwd as string } : {});
      return jsonText({
        stdout: result.stdout,
        stderr: result.stderr,
        exitCode: result.exitCode,
      });
    },
  );
}
