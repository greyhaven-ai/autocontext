import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  SYSTEM_MAP_PROJECTION,
  SYSTEM_MAP_TOPOLOGY,
  type SystemMapTopology,
} from "./system-map.js";

const SYSTEM_MAP_ASSET_PATH = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "dashboard",
  "system-map",
  "index.html",
);
const TOPOLOGY_PLACEHOLDER = "__AUTOCONTEXT_SYSTEM_MAP_TOPOLOGY__";
const PROJECTION_PLACEHOLDER = "__AUTOCONTEXT_SYSTEM_MAP_PROJECTION__";

let cachedTemplate: string | undefined;

export function renderSystemMapHtml(topology: SystemMapTopology = SYSTEM_MAP_TOPOLOGY): string {
  const template = loadSystemMapTemplate();
  const topologyJson = JSON.stringify(topology).replaceAll("<", "\\u003c");
  return template
    .replace(TOPOLOGY_PLACEHOLDER, topologyJson)
    .replace(PROJECTION_PLACEHOLDER, JSON.stringify(SYSTEM_MAP_PROJECTION));
}

function loadSystemMapTemplate(): string {
  cachedTemplate ??= readFileSync(SYSTEM_MAP_ASSET_PATH, "utf-8");
  return cachedTemplate;
}
