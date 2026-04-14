import { join } from "node:path";

import {
  loadCustomScenarios,
  registerCustomScenarios,
  type CustomScenarioEntry,
} from "../scenarios/custom-loader.js";

export interface RunCustomScenarioRegistryDeps {
  loadCustomScenarios?: (customDir: string) => Map<string, CustomScenarioEntry>;
  registerCustomScenarios?: (loaded: Map<string, CustomScenarioEntry>) => void;
}

export class RunCustomScenarioRegistry {
  readonly #knowledgeRoot: string;
  readonly #deps: RunCustomScenarioRegistryDeps;
  #entries = new Map<string, CustomScenarioEntry>();

  constructor(opts: {
    knowledgeRoot: string;
    deps?: RunCustomScenarioRegistryDeps;
  }) {
    this.#knowledgeRoot = opts.knowledgeRoot;
    this.#deps = opts.deps ?? {};
  }

  reload(): void {
    const customDir = join(this.#knowledgeRoot, "_custom_scenarios");
    const loaded = (this.#deps.loadCustomScenarios ?? loadCustomScenarios)(customDir);
    (this.#deps.registerCustomScenarios ?? registerCustomScenarios)(loaded);
    this.#entries = loaded;
  }

  get(name: string): CustomScenarioEntry | undefined {
    return this.#entries.get(name);
  }

  values(): IterableIterator<CustomScenarioEntry> {
    return this.#entries.values();
  }

  asMap(): Map<string, CustomScenarioEntry> {
    return new Map(this.#entries);
  }
}
