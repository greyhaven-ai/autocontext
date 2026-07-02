/**
 * `scenario` and `new-scenario` commands (AC-853 split of command-handlers.ts).
 */
import { parseArgs } from "node:util";
import { resolve } from "node:path";
import type { LLMProvider } from "../../types/index.js";
import { errorMessage, getProvider } from "./shared.js";

/**
 * AC-697 slice 4: `autoctx scenario` is the canonical sub-Typer group
 * for scenario management. Today it ships a single subcommand,
 * `create`, that delegates to the legacy `new-scenario` handler so
 * the scaffolding logic stays single-sourced. `new-scenario` is kept
 * as a top-level alias for backward compatibility with existing
 * scripts; the slice-1 contract pins `aliases: ["new-scenario"]` on
 * the `scenario.create` entry.
 */
export async function cmdScenario(dbPath: string): Promise<void> {
  const subArgs = process.argv.slice(3);
  if (subArgs[0] === "create") {
    // PR #999 review (P3): the slice-4 delegation routed
    // `autoctx scenario create --help` through cmdNewScenario,
    // which prints help under the legacy `autoctx new-scenario`
    // header. Intercept --help here so users asking about the
    // canonical command name see canonical text.
    const createArgs = subArgs.slice(1);
    if (createArgs.includes("--help") || createArgs.includes("-h")) {
      const { SCENARIO_CREATE_HELP_TEXT } = await import("../new-scenario-command-workflow.js");
      console.log(SCENARIO_CREATE_HELP_TEXT);
      process.exit(0);
    }
    // Rewrite argv so cmdNewScenario sees its sub-args at the
    // canonical position (process.argv.slice(3)).
    process.argv = [...process.argv.slice(0, 2), "new-scenario", ...createArgs];
    return cmdNewScenario(dbPath);
  }
  if (subArgs.length === 0 || subArgs[0] === "--help" || subArgs[0] === "-h") {
    console.log(
      "autoctx scenario -- manage scenarios.\n\n" +
        "Subcommands:\n" +
        "  create   Scaffold a new scenario from a template, family pipeline, or natural-language description.\n\n" +
        "Run `autoctx scenario create --help` for details.",
    );
    process.exit(subArgs.length === 0 ? 1 : 0);
  }
  console.error(`autoctx scenario: unknown subcommand ${JSON.stringify(subArgs[0])}`);
  process.exit(1);
}

export async function cmdNewScenario(_dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      list: { type: "boolean" },
      template: { type: "string" },
      name: { type: "string" },
      description: { type: "string", short: "d" },
      "from-spec": { type: "string" },
      "from-stdin": { type: "boolean" },
      "prompt-only": { type: "boolean" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    NEW_SCENARIO_HELP_TEXT,
    ensureNewScenarioDescription,
    executeCreatedScenarioMaterialization,
    executeImportedScenarioMaterialization,
    executeTemplateScaffoldWorkflow,
    renderTemplateList,
  } = await import("../new-scenario-command-workflow.js");

  if (values.help) {
    console.log(NEW_SCENARIO_HELP_TEXT);
    process.exit(0);
  }

  const {
    createScenarioFromDescription,
    buildScenarioCreationPrompt,
    detectScenarioFamily,
    isScenarioFamilyName,
  } = await import("../../scenarios/scenario-creator.js");
  const { TemplateLoader } = await import("../../scenarios/templates/index.js");
  const { SCENARIO_TYPE_MARKERS } = await import("../../scenarios/families.js");
  const { loadSettings } = await import("../../config/index.js");
  const validFamilies = Object.keys(SCENARIO_TYPE_MARKERS).sort();

  // Mode 0: --list
  if (values.list) {
    const loader = new TemplateLoader();
    const templates = loader.listTemplates();
    console.log(renderTemplateList({ templates, json: !!values.json }));
    return;
  }

  // Mode 0b: --template <name> --name <scenario>
  if (values.template || values.name) {
    const loader = new TemplateLoader();
    const settings = loadSettings();
    try {
      console.log(
        executeTemplateScaffoldWorkflow({
          template: values.template,
          name: values.name,
          knowledgeRoot: resolve(settings.knowledgeRoot),
          json: !!values.json,
          templateLoader: loader,
        }),
      );
    } catch (error) {
      console.error(errorMessage(error));
      process.exit(1);
    }
    return;
  }

  // Mode 1: --from-spec <file>
  if (values["from-spec"]) {
    const { readFileSync } = await import("node:fs");
    const { materializeScenario } = await import("../../scenarios/materialize.js");
    let spec: Record<string, unknown>;
    try {
      spec = JSON.parse(readFileSync(values["from-spec"], "utf-8"));
    } catch (err) {
      console.error(`Error reading spec file: ${errorMessage(err)}`);
      process.exit(1);
    }
    const settings = loadSettings();
    try {
      console.log(
        await executeImportedScenarioMaterialization({
          spec,
          detectScenarioFamily,
          isScenarioFamilyName,
          validFamilies,
          materializeScenario,
          knowledgeRoot: resolve(settings.knowledgeRoot),
          json: !!values.json,
        }),
      );
    } catch (error) {
      console.error(errorMessage(error));
      process.exit(1);
    }
    return;
  }

  // Mode 2: --from-stdin
  if (values["from-stdin"]) {
    const { materializeScenario } = await import("../../scenarios/materialize.js");
    const chunks: Buffer[] = [];
    for await (const chunk of process.stdin) {
      chunks.push(chunk as Buffer);
    }
    const raw = Buffer.concat(chunks).toString("utf-8");
    let spec: Record<string, unknown>;
    try {
      spec = JSON.parse(raw);
    } catch {
      console.error("Error: stdin must contain valid JSON");
      process.exit(1);
    }
    const settings = loadSettings();
    try {
      console.log(
        await executeImportedScenarioMaterialization({
          spec,
          detectScenarioFamily,
          isScenarioFamilyName,
          validFamilies,
          materializeScenario,
          knowledgeRoot: resolve(settings.knowledgeRoot),
          json: !!values.json,
        }),
      );
    } catch (error) {
      console.error(errorMessage(error));
      process.exit(1);
    }
    return;
  }

  // Mode 3: --prompt-only (output the prompt, no LLM call)
  if (values["prompt-only"]) {
    let description: string;
    try {
      description = ensureNewScenarioDescription({
        description: values.description,
        errorMessage: "Error: --description is required with --prompt-only",
      });
    } catch (error) {
      console.error(errorMessage(error));
      process.exit(1);
    }
    const prompt = buildScenarioCreationPrompt(description);
    console.log(prompt);
    return;
  }

  // Default: --description mode (requires LLM)
  let description: string;
  try {
    description = ensureNewScenarioDescription({
      description: values.description,
      errorMessage:
        "Error: --list, --template, --description, --from-spec, --from-stdin, or --prompt-only is required",
    });
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  let provider: LLMProvider;
  try {
    const result = await getProvider();
    provider = result.provider;
  } catch {
    const { DeterministicProvider } = await import("../../providers/deterministic.js");
    provider = new DeterministicProvider();
  }

  try {
    const result = await createScenarioFromDescription(description, provider);

    // Materialize the created scenario to disk (AC-433)
    const { materializeScenario } = await import("../../scenarios/materialize.js");
    const settings = loadSettings();
    console.log(
      await executeCreatedScenarioMaterialization({
        created: result,
        materializeScenario,
        knowledgeRoot: resolve(settings.knowledgeRoot),
        json: !!values.json,
      }),
    );
  } catch (error) {
    console.error(errorMessage(error));
    provider.close?.();
    process.exit(1);
  } finally {
    provider.close?.();
  }
}
