import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { basename, isAbsolute, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import {
  activateRuntimeComponent,
  type RuntimeComponentLifecycleEventSink,
  type RuntimeComponentScope,
} from "../runtimes/component-lifecycle.js";
import { ExtensionAPI, HookBus } from "./hooks.js";

type ExtensionCallable = (api?: ExtensionAPI) => unknown | Promise<unknown>;

export interface LoadExtensionComponentsOptions {
  eventSink?: RuntimeComponentLifecycleEventSink;
}

export interface LoadedExtensionComponent {
  readonly ref: string;
  readonly scope: RuntimeComponentScope;
  unload(): Promise<void>;
}

export async function loadExtensions(
  refs: string | Iterable<string>,
  bus: HookBus,
): Promise<string[]> {
  const loaded = await loadExtensionComponents(refs, bus);
  return loaded.map((component) => component.ref);
}

export async function loadExtensionComponents(
  refs: string | Iterable<string>,
  bus: HookBus,
  options: LoadExtensionComponentsOptions = {},
): Promise<LoadedExtensionComponent[]> {
  const loaded: LoadedExtensionComponent[] = [];
  try {
    for (const ref of splitRefs(refs)) {
      const target = await loadTarget(ref);
      const scope = await activateRuntimeComponent(
        {
          componentId: extensionComponentId(ref),
          eventSink: options.eventSink,
        },
        async (componentScope) => {
          const api = new ExtensionAPI(bus, componentScope);
          await invokeExtension(target, api);
          bus.loadedExtensions.push(ref);
          componentScope.defer(() => removeLoadedExtension(bus, ref));
        },
      );
      loaded.push({
        ref,
        scope,
        unload: () => scope.dispose(),
      });
    }
  } catch (loadError) {
    const cleanupErrors: unknown[] = [];
    for (const component of [...loaded].reverse()) {
      try {
        await component.unload();
      } catch (cleanupError) {
        cleanupErrors.push(cleanupError);
      }
    }
    if (cleanupErrors.length > 0) {
      throw new AggregateError(
        [loadError, ...cleanupErrors],
        "extension batch activation and cleanup failed",
      );
    }
    throw loadError;
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

function extensionComponentId(ref: string): string {
  const [moduleRef] = splitModuleRef(ref);
  const label = basename(moduleRef).replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 80) || "module";
  const digest = createHash("sha256").update(ref).digest("hex").slice(0, 12);
  return `extension:${label}:${digest}`;
}

function removeLoadedExtension(bus: HookBus, ref: string): void {
  const index = bus.loadedExtensions.lastIndexOf(ref);
  if (index !== -1) bus.loadedExtensions.splice(index, 1);
}
