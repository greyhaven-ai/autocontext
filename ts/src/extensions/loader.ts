import { existsSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { ExtensionAPI, HookBus } from "./hooks.js";

type ExtensionCallable = (api?: ExtensionAPI) => unknown | Promise<unknown>;

export async function loadExtensions(
  refs: string | Iterable<string>,
  bus: HookBus,
): Promise<string[]> {
  const loaded: string[] = [];
  const api = new ExtensionAPI(bus);
  for (const ref of splitRefs(refs)) {
    const target = await loadTarget(ref);
    await invokeExtension(target, api);
    loaded.push(ref);
    bus.loadedExtensions.push(ref);
  }
  return loaded;
}

export async function initializeHookBus(opts: {
  extensions?: string | Iterable<string> | null;
  failFast?: boolean;
} = {}): Promise<{ hookBus: HookBus; loadedExtensions: string[] }> {
  const hookBus = new HookBus({ failFast: opts.failFast ?? false });
  const loadedExtensions = opts.extensions
    ? await loadExtensions(opts.extensions, hookBus)
    : [];
  return { hookBus, loadedExtensions };
}

function splitRefs(refs: string | Iterable<string>): string[] {
  if (typeof refs === "string") {
    return refs.split(",").map((part) => part.trim()).filter(Boolean);
  }
  return [...refs].map((part) => String(part).trim()).filter(Boolean);
}

async function loadTarget(ref: string): Promise<unknown> {
  const [moduleRef, attrPath] = splitModuleRef(ref);
  const moduleValue = await loadModule(moduleRef);
  if (attrPath) {
    let target: unknown = moduleValue;
    for (const part of attrPath.split(".")) {
      if (!isRecord(target)) {
        throw new Error(`extension target ${ref} could not resolve ${part}`);
      }
      target = target[part];
    }
    return target;
  }
  if (isRecord(moduleValue)) {
    for (const name of ["register", "configure", "setup"]) {
      const target = moduleValue[name];
      if (isCallable(target)) {
        return target;
      }
    }
  }
  return moduleValue;
}

function splitModuleRef(ref: string): [string, string] {
  const colonIndex = ref.indexOf(":");
  if (colonIndex < 0) {
    return [ref, ""];
  }
  return [ref.slice(0, colonIndex), ref.slice(colonIndex + 1)];
}

async function loadModule(moduleRef: string): Promise<unknown> {
  const pathLike = isPathLike(moduleRef);
  const resolved = pathLike ? resolve(moduleRef) : moduleRef;
  const specifier = pathLike ? pathToFileURL(resolved).href : moduleRef;
  try {
    return await import(specifier);
  } catch (error) {
    const label = pathLike ? resolved : moduleRef;
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`could not load extension ${label}: ${message}`);
  }
}

function isPathLike(ref: string): boolean {
  if (ref.startsWith(".") || ref.startsWith("~") || isAbsolute(ref)) {
    return true;
  }
  if (/\.[cm]?[jt]s$/.test(ref)) {
    return true;
  }
  return existsSync(ref);
}

async function invokeExtension(target: unknown, api: ExtensionAPI): Promise<void> {
  if (isRecord(target)) {
    const register = target.register;
    if (isCallable(register)) {
      await callExtension(register, api);
      return;
    }
  }
  if (isCallable(target)) {
    const result = await callExtension(target, api);
    if (isRecord(result) && isCallable(result.register)) {
      await callExtension(result.register, api);
    }
    return;
  }
  throw new Error("extension module must export register, configure, setup, or a callable target");
}

async function callExtension(func: ExtensionCallable, api: ExtensionAPI): Promise<unknown> {
  return func.length === 0 ? await func() : await func(api);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCallable(value: unknown): value is ExtensionCallable {
  return typeof value === "function";
}
