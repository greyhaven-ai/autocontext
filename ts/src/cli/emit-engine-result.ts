/**
 * Single point of truth for CLI engine-result emission (AC-526).
 *
 * Every engine-driven command (`simulate`, `investigate`, `analyze`,
 * `train`, compare, replay) delegates to this helper instead of
 * duplicating the json/text × success/failure dispatch.
 */

export interface EngineResultLike {
  status: string;
  error?: string;
}

export interface EmitOptions<T extends EngineResultLike> {
  json: boolean;
  label: string;
  renderSuccess: (result: T) => void;
  exitFn?: (code: number) => never;
  writeJson?: (payload: unknown) => void;
  writeError?: (msg: string) => void;
}

const FAILURE_STATUSES = new Set(["failed", "error", "incomplete"]);

export function isFailureStatus(status: string): boolean {
  return FAILURE_STATUSES.has(status);
}

export function emitEngineResult<T extends EngineResultLike>(
  result: T,
  opts: EmitOptions<T>,
): void {
  const exit = opts.exitFn ?? ((code: number) => process.exit(code));
  const writeJson =
    opts.writeJson ??
    ((payload: unknown) => console.log(JSON.stringify(payload, null, 2)));
  const writeError = opts.writeError ?? ((msg: string) => console.error(msg));

  if (opts.json) {
    writeJson(result);
    if (isFailureStatus(result.status)) {
      exit(1);
    }
    return;
  }

  if (isFailureStatus(result.status)) {
    const suffix = result.error ? `: ${result.error}` : "";
    writeError(`${opts.label} ${result.status}${suffix}`);
    exit(1);
    return;
  }

  opts.renderSuccess(result);
}
