export function jsonSafeRecord(value: Record<string, unknown> | undefined): Record<string, unknown> {
  if (!value) return {};
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [String(key), jsonSafeValue(item)]),
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
      return String(value);
    case "object":
      return jsonSafeObject(value, seen);
  }
}

function jsonSafeObject(value: object, seen: WeakSet<object>): unknown {
  if (seen.has(value)) {
    return String(value);
  }
  seen.add(value);
  try {
    if (Array.isArray(value)) {
      return value.map((item) => jsonSafeValue(item, seen));
    }
    const toJSON = getToJSON(value);
    if (toJSON) {
      return jsonSafeValue(toJSON(), seen);
    }
    if (isPlainRecord(value)) {
      return Object.fromEntries(
        Object.entries(value).map(([key, item]) => [String(key), jsonSafeValue(item, seen)]),
      );
    }
    return String(value);
  } finally {
    seen.delete(value);
  }
}

function isPlainRecord(value: object): value is Record<string, unknown> {
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function getToJSON(value: object): (() => unknown) | undefined {
  const toJSON = Reflect.get(value, "toJSON");
  if (typeof toJSON !== "function") return undefined;
  return () => toJSON.call(value);
}
