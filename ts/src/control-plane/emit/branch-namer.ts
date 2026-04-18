// Branch-name generator for PR emission (§9.3).
//
// Format: autocontext/<scenario>/<actuatorType>/<artifactId-first-8-chars>
//
// Deterministic (pure function of Artifact), greppable ("autocontext/" prefix
// is trivial to filter on in branch lists), and collision-safe for typical
// operator workflows: two candidates produced for the same scenario/actuatorType
// would only collide if their ULIDs shared an 8-char prefix — the first 6 chars
// of a ULID encode a ~millisecond timestamp, so a collision would require two
// candidates generated within the same ~35-minute bucket. In that (rare) case
// the operator sees a `git push` failure on the pre-existing branch; we keep
// the prefix short for greppability and let git handle the collision signal.

import type { Artifact } from "../contract/types.js";

export function branchNameFor(artifact: Artifact): string {
  const shortId = artifact.id.slice(0, 8);
  return `autocontext/${artifact.scenario}/${artifact.actuatorType}/${shortId}`;
}
