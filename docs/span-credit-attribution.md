# Span-level credit attribution (AC-797)

`context_attribution` defaults to `component`. Set `AUTOCONTEXT_CONTEXT_ATTRIBUTION=span` (or `contextAttribution: "span"` in TypeScript settings) to attach span-level attribution metadata to credit-assignment records.

Span IDs are stable hashes of `{source}:{normalized span text}` for non-empty hint, playbook, analysis, and context lines. Span credit is marked `component_correlated`: it is useful for ranking or demoting noisy spans, but it is not proof that a line caused the score movement.

Prompt attribution summaries include the top correlated spans when span mode is present. Low or negative credit marks spans as demotion candidates without deleting the parent component.
