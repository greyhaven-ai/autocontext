export function getStringValue(
  spec: Record<string, unknown>,
  ...keys: string[]
): string | null {
  for (const key of keys) {
    const value = spec[key];
    if (typeof value === "string" && value.trim().length > 0) {
      return value.trim();
    }
  }
  return null;
}

export function getNumberValue(
  spec: Record<string, unknown>,
  ...keys: string[]
): number | null {
  for (const key of keys) {
    const value = spec[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }
  return null;
}

export function getStringArrayValue(
  spec: Record<string, unknown>,
  ...keys: string[]
): string[] | null {
  for (const key of keys) {
    const value = spec[key];
    if (
      Array.isArray(value) &&
      value.every((entry) => typeof entry === "string")
    ) {
      return value;
    }
  }
  return null;
}

export function getRecordArrayValue(
  spec: Record<string, unknown>,
  ...keys: string[]
): Array<Record<string, unknown>> | null {
  for (const key of keys) {
    const value = spec[key];
    if (
      Array.isArray(value) &&
      value.every((entry) => entry != null && typeof entry === "object")
    ) {
      return value as Array<Record<string, unknown>>;
    }
  }
  return null;
}
