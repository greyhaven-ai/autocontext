---
description: Iteratively improve recent work through judge-guided feedback loops
---
Take the most recent code or output I produced and improve it using the
`autocontext_improve` tool with:
- **task_prompt**: summarize what I was trying to accomplish
- **initial_output**: the code or output to improve
- **rubric**: "Correctness, completeness, code quality, error handling, edge cases, documentation"
- **max_rounds**: 3
- **quality_threshold**: 0.85

Show me the final improved version and explain what changed.
