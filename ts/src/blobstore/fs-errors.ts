export function isNotFoundError(error: unknown): boolean {
  return (
    typeof error === "object"
    && error !== null
    && "code" in error
    && (error as { code?: unknown }).code === "ENOENT"
  );
}

export function fsError(action: string, target: string, error: unknown): Error {
  const detail = error instanceof Error ? error.message : String(error);
  return new Error(`Failed to ${action} '${target}': ${detail}`, { cause: error });
}
