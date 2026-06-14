import type {
  AgentAppFetchSessionEventLogSnapshot,
  AgentAppFetchSessionEventRecord,
  AgentAppFetchSessionEventStore,
} from "./session-event-store.js";
import type { AgentAppFetchWorkspaceStore } from "./workspace-store.js";
import { createAgentAppFetchWorkspaceEnv } from "./workspace-store.js";

export interface AgentAppFetchStoreConformanceCase {
  name: string;
  run(): Promise<void>;
}

export interface AgentAppFetchWorkspaceStoreConformanceOptions {
  createStore(): MaybePromise<AgentAppFetchWorkspaceStore>;
}

export interface AgentAppFetchSessionEventStoreConformanceOptions {
  createStore(): MaybePromise<AgentAppFetchSessionEventStore>;
}

type MaybePromise<T> = T | Promise<T>;

export function createAgentAppFetchWorkspaceStoreConformanceCases(
  options: AgentAppFetchWorkspaceStoreConformanceOptions,
): AgentAppFetchStoreConformanceCase[] {
  return [
    {
      name: "workspace store read-your-writes and lexicographic listing",
      run: () => withWorkspaceStore(options, assertWorkspaceReadYourWritesAndListing),
    },
    {
      name: "workspace store byte cloning boundaries",
      run: () => withWorkspaceStore(options, assertWorkspaceByteCloning),
    },
    {
      name: "workspace root recursive removal preserves root",
      run: () => withWorkspaceStore(options, assertWorkspaceRootRemoval),
    },
    {
      name: "workspace env shell execution fails closed",
      run: () => withWorkspaceStore(options, assertWorkspaceShellFailsClosed),
    },
  ];
}

export async function runAgentAppFetchWorkspaceStoreConformance(
  options: AgentAppFetchWorkspaceStoreConformanceOptions,
): Promise<void> {
  for (const testCase of createAgentAppFetchWorkspaceStoreConformanceCases(options)) {
    await testCase.run();
  }
}

export function createAgentAppFetchSessionEventStoreConformanceCases(
  options: AgentAppFetchSessionEventStoreConformanceOptions,
): AgentAppFetchStoreConformanceCase[] {
  return [
    {
      name: "session event store append idempotency and replay ordering",
      run: () => withSessionEventStore(options, assertSessionAppendIdempotencyAndOrdering),
    },
    {
      name: "session event store deep-clones metadata and payloads",
      run: () => withSessionEventStore(options, assertSessionDeepCloning),
    },
    {
      name: "session event store preserves child-session links",
      run: () => withSessionEventStore(options, assertSessionChildLinks),
    },
  ];
}

export async function runAgentAppFetchSessionEventStoreConformance(
  options: AgentAppFetchSessionEventStoreConformanceOptions,
): Promise<void> {
  for (const testCase of createAgentAppFetchSessionEventStoreConformanceCases(options)) {
    await testCase.run();
  }
}

async function withWorkspaceStore(
  options: AgentAppFetchWorkspaceStoreConformanceOptions,
  run: (store: AgentAppFetchWorkspaceStore) => Promise<void>,
): Promise<void> {
  const store = await options.createStore();
  await run(store);
}

async function withSessionEventStore(
  options: AgentAppFetchSessionEventStoreConformanceOptions,
  run: (store: AgentAppFetchSessionEventStore) => Promise<void>,
): Promise<void> {
  const store = await options.createStore();
  try {
    await run(store);
  } finally {
    await store.close?.();
  }
}

async function assertWorkspaceReadYourWritesAndListing(
  store: AgentAppFetchWorkspaceStore,
): Promise<void> {
  await store.mkdir("/conformance", { recursive: true });
  await store.writeFile("/conformance/zeta.txt", bytes(3));
  await store.writeFile("/conformance/alpha.txt", bytes(1));

  assert(await store.exists("/conformance/alpha.txt"), "expected written file to exist");
  assertArrayEqual(
    await store.readdir("/conformance"),
    ["alpha.txt", "zeta.txt"],
    "expected lexicographic directory listing",
  );
  const stat = await store.stat("/conformance/alpha.txt");
  assert(stat.kind === "file", "expected written path to stat as a file");
  assert(stat.size === 1, "expected file stat size to match written bytes");
  assertArrayEqual(
    [...(await store.readFile("/conformance/alpha.txt"))],
    [1],
    "expected read-your-writes byte content",
  );
}

async function assertWorkspaceByteCloning(store: AgentAppFetchWorkspaceStore): Promise<void> {
  const original = new Uint8Array([1, 2, 3]);
  await store.writeFile("/conformance-bytes.bin", original);
  original[0] = 9;

  const firstRead = await store.readFile("/conformance-bytes.bin");
  assertArrayEqual([...firstRead], [1, 2, 3], "expected store to clone bytes on write");
  firstRead[1] = 8;

  assertArrayEqual(
    [...(await store.readFile("/conformance-bytes.bin"))],
    [1, 2, 3],
    "expected store to clone bytes on read",
  );
}

async function assertWorkspaceRootRemoval(store: AgentAppFetchWorkspaceStore): Promise<void> {
  await store.mkdir("/conformance-root/child", { recursive: true });
  await store.writeFile("/conformance-root/child/data.txt", bytes(1, 2, 3));
  await store.writeFile("/root-conformance.txt", bytes(4));

  await store.rm("/", { recursive: true });

  const rootStat = await store.stat("/");
  assert(rootStat.kind === "directory", "expected recursive root removal to preserve root");
  assert(!(await store.exists("/conformance-root")), "expected root removal to clear dirs");
  assert(!(await store.exists("/root-conformance.txt")), "expected root removal to clear files");
  assertArrayEqual(await store.readdir("/"), [], "expected root listing to be empty");
}

async function assertWorkspaceShellFailsClosed(store: AgentAppFetchWorkspaceStore): Promise<void> {
  const workspace = createAgentAppFetchWorkspaceEnv({ store });
  await expectRejects(
    () => workspace.exec("echo should-not-run"),
    "Runtime command execution is unavailable in the generic Fetch agent app workspace",
    "expected Fetch workspace env shell execution to fail closed",
  );
}

async function assertSessionAppendIdempotencyAndOrdering(
  store: AgentAppFetchSessionEventStore,
): Promise<void> {
  assertSessionCapabilities(store);
  const snapshot = sessionSnapshot("conformance-session", {
    metadata: { goal: "ordering" },
    events: [
      sessionEvent("evt-2", "conformance-session", 1, "assistant_message", {
        text: "world",
      }),
      sessionEvent("evt-1", "conformance-session", 0, "prompt_submitted", {
        prompt: "hello",
      }),
    ],
  });

  const first = await store.append(snapshot);
  assert(first.appended === 2, "expected first append to store both events");

  const duplicate = await store.append(snapshot);
  assert(duplicate.appended === 0, "expected duplicate append to be idempotent");
  assertArraySetEqual(
    duplicate.duplicateEventIds,
    ["evt-1", "evt-2"],
    "expected duplicate event ids to be reported",
  );

  const loaded = await store.load("conformance-session");
  assert(loaded !== null, "expected appended session to load");
  assertArrayEqual(
    loaded.events.map((event) => event.eventId),
    ["evt-1", "evt-2"],
    "expected replay ordered by per-session sequence",
  );
  assertArrayEqual(
    loaded.events.map((event) => event.sequence),
    [0, 1],
    "expected replay sequences to be preserved",
  );
}

async function assertSessionDeepCloning(store: AgentAppFetchSessionEventStore): Promise<void> {
  const metadata = { nested: { owner: "original" } };
  const event = sessionEvent("evt-clone", "conformance-clone", 0, "assistant_message", {
    nested: { text: "original" },
  });

  await store.append(
    sessionSnapshot("conformance-clone", {
      metadata,
      events: [event],
    }),
  );
  metadata.nested.owner = "mutated after append";
  (event.payload.nested as { text: string }).text = "mutated after append";

  const firstLoad = await store.load("conformance-clone");
  assert(firstLoad !== null, "expected cloned session to load");
  assertDeepEqual(
    firstLoad.metadata,
    { nested: { owner: "original" } },
    "expected metadata to be cloned on append",
  );
  assertDeepEqual(
    firstLoad.events[0]?.payload,
    { nested: { text: "original" } },
    "expected event payload to be cloned on append",
  );

  (firstLoad.metadata.nested as { owner: string }).owner = "mutated after load";
  (firstLoad.events[0]!.payload.nested as { text: string }).text = "mutated after load";

  const secondLoad = await store.load("conformance-clone");
  assert(secondLoad !== null, "expected cloned session to reload");
  assertDeepEqual(
    secondLoad.metadata,
    { nested: { owner: "original" } },
    "expected metadata to be cloned on load",
  );
  assertDeepEqual(
    secondLoad.events[0]?.payload,
    { nested: { text: "original" } },
    "expected event payload to be cloned on load",
  );
}

async function assertSessionChildLinks(store: AgentAppFetchSessionEventStore): Promise<void> {
  const parent = sessionSnapshot("conformance-parent", {
    events: [sessionEvent("evt-parent", "conformance-parent", 0, "prompt_submitted")],
  });
  const child = sessionSnapshot("conformance-child", {
    parentSessionId: "conformance-parent",
    taskId: "child-task",
    workerId: "worker-1",
    events: [
      sessionEvent("evt-child", "conformance-child", 0, "assistant_message", {
        text: "child complete",
      }),
    ],
  });

  await store.append(parent);
  await store.append(child);

  const loadedChild = await store.load("conformance-child");
  assert(loadedChild !== null, "expected child session to load");
  assert(
    loadedChild.parentSessionId === "conformance-parent",
    "expected child parentSessionId to be replay-visible",
  );
  assert(loadedChild.taskId === "child-task", "expected child taskId to be replay-visible");
  assert(loadedChild.workerId === "worker-1", "expected child workerId to be replay-visible");

  if (store.listChildren) {
    const children = await store.listChildren("conformance-parent");
    assertArrayEqual(
      children.map((snapshot) => snapshot.sessionId),
      ["conformance-child"],
      "expected listChildren to expose child-session linkage when implemented",
    );
  }
}

function assertSessionCapabilities(store: AgentAppFetchSessionEventStore): void {
  assert(
    store.capabilities.ordering === "per_session_sequence",
    "expected session store ordering capability to be per_session_sequence",
  );
  assert(
    store.capabilities.idempotency === "event_id",
    "expected session store idempotency capability to be event_id",
  );
  assert(
    store.capabilities.consistency === "read_your_writes_after_append",
    "expected session store consistency capability to be read_your_writes_after_append",
  );
}

function sessionSnapshot(
  sessionId: string,
  options: Partial<AgentAppFetchSessionEventLogSnapshot> = {},
): AgentAppFetchSessionEventLogSnapshot {
  const createdAt = options.createdAt ?? "2026-06-14T00:00:00.000Z";
  return {
    sessionId,
    parentSessionId: options.parentSessionId ?? "",
    taskId: options.taskId ?? "",
    workerId: options.workerId ?? "",
    metadata: options.metadata ?? {},
    events: options.events ?? [],
    createdAt,
    updatedAt: options.updatedAt ?? createdAt,
  };
}

function sessionEvent(
  eventId: string,
  sessionId: string,
  sequence: number,
  eventType: string,
  payload: Record<string, unknown> = {},
): AgentAppFetchSessionEventRecord {
  return {
    eventId,
    sessionId,
    sequence,
    eventType,
    timestamp: `2026-06-14T00:00:0${sequence}.000Z`,
    payload,
    parentSessionId: "",
    taskId: "",
    workerId: "",
  };
}

function bytes(...values: number[]): Uint8Array {
  return new Uint8Array(values);
}

async function expectRejects(
  run: () => MaybePromise<unknown>,
  expectedMessage: string,
  assertionMessage: string,
): Promise<void> {
  try {
    await run();
  } catch (error) {
    assert(
      error instanceof Error && error.message.includes(expectedMessage),
      `${assertionMessage}: received ${String(error)}`,
    );
    return;
  }
  throw new Error(assertionMessage);
}

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`Agent app Fetch store conformance failed: ${message}`);
}

function assertArrayEqual<T>(actual: readonly T[], expected: readonly T[], message: string): void {
  assertDeepEqual(actual, expected, message);
}

function assertArraySetEqual<T>(
  actual: readonly T[],
  expected: readonly T[],
  message: string,
): void {
  assertDeepEqual([...actual].sort(), [...expected].sort(), message);
}

function assertDeepEqual(actual: unknown, expected: unknown, message: string): void {
  const renderedActual = JSON.stringify(actual);
  const renderedExpected = JSON.stringify(expected);
  assert(
    renderedActual === renderedExpected,
    `${message}: expected ${renderedExpected}, received ${renderedActual}`,
  );
}
