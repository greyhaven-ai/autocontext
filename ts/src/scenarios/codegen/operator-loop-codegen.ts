/**
 * Operator-loop family codegen — intentionally unsupported (AC-436, AC-432).
 * Mirrors Python's autocontext/scenarios/custom/operator_loop_codegen.py.
 */

import { CodegenUnsupportedFamilyError } from "./runtime.js";

export function generateOperatorLoopSource(
  _spec: Record<string, unknown>,
  _name: string,
): string {
  throw new CodegenUnsupportedFamilyError("operator_loop");
}
