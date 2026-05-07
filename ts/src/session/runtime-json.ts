export function jsonSafeRecord(value: Record<string, unknown> | undefined): Record<string, unknown> {
  if (!value) return {};
  const entries = safeEntries(value);
  if (!entries) return { value: safeString(value) };
  return Object.fromEntries(
    entries.map(([key, item]) => [String(key), jsonSafeValue(item)]),
  );
}

function jsonSafeValue(value: unknown, seen = new WeakSet<object>()): unknown {
  if (value === null) return null;

  switch (typeof value) {
    case "string":
    case "boolean":
      return value;
    case "number":
      return Number.isFinite(value) ? value : String(value);
    case "bigint":
      return value.toString();
    case "undefined":
    case "function":
    case "symbol":
      return safeString(value);
    case "object":
      return jsonSafeObject(value, seen);
  }
}

function jsonSafeObject(value: object, seen: WeakSet<object>): unknown {
  if (seen.has(value)) {
    return safeString(value);
  }
  seen.add(value);
  try {
    if (Array.isArray(value)) {
      return value.map((item) => jsonSafeValue(item, seen));
    }
    const toJSON = readToJSON(value);
    if (toJSON.status === "value") {
      return jsonSafeValue(toJSON.value, seen);
    }
    if (toJSON.status === "failed") {
      return safeString(value);
    }
    if (isPlainRecord(value)) {
      const entries = safeEntries(value);
      if (!entries) return safeString(value);
      return Object.fromEntries(
        entries.map(([key, item]) => [String(key), jsonSafeValue(item, seen)]),
      );
    }
    return safeString(value);
  } finally {
    seen.delete(value);
  }
}

function isPlainRecord(value: object): value is Record<string, unknown> {
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

type ToJSONReadResult =
  | { status: "missing" }
  | { status: "value"; value: unknown }
  | { status: "failed" };

function readToJSON(value: object): ToJSONReadResult {
  let toJSON: unknown;
  try {
    toJSON = Reflect.get(value, "toJSON");
  } catch {
    return { status: "failed" };
  }
  if (typeof toJSON !== "function") {
    return { status: "missing" };
  }
  try {
    return { status: "value", value: Reflect.apply(toJSON, value, []) };
  } catch {
    return { status: "failed" };
  }
}

function safeEntries(value: object): Array<[string, unknown]> | undefined {
  try {
    return Object.entries(value);
  } catch {
    return undefined;
  }
}

function safeString(value: unknown): string {
  try {
    return String(value);
  } catch {
    return "[unserializable]";
  }
}
