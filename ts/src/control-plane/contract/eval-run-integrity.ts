export interface EvalRunIntegrityCarrier {
  readonly integrity?: { readonly status?: unknown } | null;
}

export function evalRunIntegrityStatus(run: EvalRunIntegrityCarrier): unknown {
  if (run.integrity === undefined || run.integrity === null) {
    return "clean";
  }
  return run.integrity.status;
}

export function describeNonCleanEvalRunIntegrity(
  run: EvalRunIntegrityCarrier,
  label: string,
): string | null {
  const status = evalRunIntegrityStatus(run);
  if (status === "clean") {
    return null;
  }
  return `${label} EvalRun integrity status is ${String(status)}`;
}
