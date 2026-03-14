import type { ArtifactEditingSpec } from "./artifact-editing-spec.js";
import { parseRawArtifactEditingSpec } from "./artifact-editing-spec.js";

export const ARTIFACT_SPEC_START = "<!-- ARTIFACT_EDITING_SPEC_START -->";
export const ARTIFACT_SPEC_END = "<!-- ARTIFACT_EDITING_SPEC_END -->";

const EXAMPLE_SPEC = {
  task_description: "Update a YAML service config to add a database section without changing unrelated settings.",
  rubric:
    "Evaluate correctness of the edited artifacts, satisfaction of validation rules, and minimal unnecessary changes.",
  validation_rules: [
    'config/app.yaml must contain "database:"',
    'config/app.yaml must contain "host:"',
    'config/app.yaml must contain "port:"',
  ],
  artifacts: [
    {
      path: "config/app.yaml",
      content: "app:\n  name: myapp\n  port: 8080\n",
      content_type: "yaml",
    },
  ],
};

export const ARTIFACT_EDITING_DESIGNER_SYSTEM = `You are a scenario designer for autocontext.
Given a natural-language request for an artifact-editing task, produce an ArtifactEditingSpec JSON.

Wrap the output in delimiters:
${ARTIFACT_SPEC_START}
{ ... }
${ARTIFACT_SPEC_END}

Schema:
{
  "task_description": "what the agent should change in the artifacts",
  "rubric": "how the final edited artifacts should be judged",
  "validation_rules": ["path/to/file must contain \\"snippet\\""],
  "artifacts": [
    {
      "path": "config/app.yaml",
      "content": "current file contents",
      "content_type": "yaml"
    }
  ]
}

Rules:
- model the task around editing concrete artifacts, not writing prose about them
- include at least one artifact with realistic initial content
- express validation rules as path-scoped must-contain or must-not-contain checks when possible
- keep the rubric focused on artifact correctness, validator success, and precision of edits

Example:
${ARTIFACT_SPEC_START}
${JSON.stringify(EXAMPLE_SPEC, null, 2)}
${ARTIFACT_SPEC_END}
`;

export function parseArtifactEditingSpec(text: string): ArtifactEditingSpec {
  const startIdx = text.indexOf(ARTIFACT_SPEC_START);
  const endIdx = text.indexOf(ARTIFACT_SPEC_END);
  if (startIdx === -1 || endIdx === -1 || endIdx <= startIdx) {
    throw new Error("response does not contain ARTIFACT_EDITING_SPEC delimiters");
  }
  const raw = text.slice(startIdx + ARTIFACT_SPEC_START.length, endIdx).trim();
  return parseRawArtifactEditingSpec(JSON.parse(raw) as Record<string, unknown>);
}

export async function designArtifactEditing(
  description: string,
  llmFn: (system: string, user: string) => Promise<string>,
): Promise<ArtifactEditingSpec> {
  return parseArtifactEditingSpec(
    await llmFn(ARTIFACT_EDITING_DESIGNER_SYSTEM, `User description:\n${description}`),
  );
}
