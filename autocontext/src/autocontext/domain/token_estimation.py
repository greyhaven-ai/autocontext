"""Dependency-free token estimation shared by prompt and compaction domains."""


def estimate_tokens(text: str) -> int:
    """Estimate token count using the repository's char/4 heuristic."""
    return len(text) // 4
