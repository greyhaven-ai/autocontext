import type {
  AblationRequirement,
  AblationTarget,
  AblationVerificationAssessment,
  EvalRun,
} from "./types.js";

export const ABLATION_TARGETS = ["strategy", "harness"] as const;

export const DEFAULT_ABLATION_REQUIREMENT: AblationRequirement = {
  required: false,
  targets: ABLATION_TARGETS,
};

export function isAblationTarget(value: string): value is AblationTarget {
  return value === "strategy" || value === "harness";
}

export function normalizeAblationRequirement(
  requirement: AblationRequirement | undefined,
): AblationRequirement {
  if (requirement === undefined) return DEFAULT_ABLATION_REQUIREMENT;
  return {
    required: requirement.required,
    targets: uniqueTargets(requirement.targets),
  };
}

export function assessAblationVerification(
  run: EvalRun,
  label: string,
  requirementInput: AblationRequirement | undefined,
): AblationVerificationAssessment {
  const requirement = normalizeAblationRequirement(requirementInput);
  const coveredTargets = uniqueTargets(run.ablationVerification?.targets ?? []);
  if (!requirement.required) {
    return {
      required: false,
      status: "not-required",
      requiredTargets: requirement.targets,
      coveredTargets,
      missingTargets: [],
    };
  }

  const missingTargets = requirement.targets.filter((target) => !coveredTargets.includes(target));
  if (run.ablationVerification === undefined) {
    return {
      required: true,
      status: "missing",
      requiredTargets: requirement.targets,
      coveredTargets,
      missingTargets,
      reason: `${label} EvalRun is missing required ablation verification`,
    };
  }

  if (run.ablationVerification.status !== "passed") {
    return {
      required: true,
      status: run.ablationVerification.status,
      requiredTargets: requirement.targets,
      coveredTargets,
      missingTargets,
      reason: `${label} ablation verification status is ${run.ablationVerification.status}`,
    };
  }

  if (missingTargets.length > 0) {
    return {
      required: true,
      status: "incomplete",
      requiredTargets: requirement.targets,
      coveredTargets,
      missingTargets,
      reason: `${label} ablation verification is missing required targets: ${missingTargets.join(", ")}`,
    };
  }

  return {
    required: true,
    status: "passed",
    requiredTargets: requirement.targets,
    coveredTargets,
    missingTargets: [],
  };
}

export function describeAblationVerificationIssue(
  run: EvalRun,
  label: string,
  requirement: AblationRequirement | undefined,
): string | null {
  const assessment = assessAblationVerification(run, label, requirement);
  return assessment.status === "passed" || assessment.status === "not-required"
    ? null
    : (assessment.reason ?? `${label} ablation verification did not pass`);
}

function uniqueTargets(targets: readonly AblationTarget[]): readonly AblationTarget[] {
  return ABLATION_TARGETS.filter((target) => targets.includes(target));
}
