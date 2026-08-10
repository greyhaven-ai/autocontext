"""Emit docs/role-output-schemas.json from the pydantic role models.

The models are the single source of truth. TypeScript reads the generated
artifact rather than retyping the schemas, because retyping is exactly how the
two drift: a hand-copied version of the analyst schema has already been written
twice during AC-913/AC-929, once losing the field descriptions (which ride to
the model and shape generation) and once losing `minItems`, which is the
difference between "the key exists" and "the section has content".

Usage:
    python scripts/generate_role_output_schemas.py           # write
    python scripts/generate_role_output_schemas.py --check   # CI gate
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "docs" / "role-output-schemas.json"


def build() -> str:
    sys.path.insert(0, str(REPO_ROOT / "autocontext" / "src"))
    from autocontext.agents.role_schemas import ANALYST_SCHEMA, ARCHITECT_SCHEMA, COACH_SCHEMA

    payload = {
        "contract": (
            "Role output schemas, generated from the pydantic models in "
            "autocontext/agents/role_schemas.py. Both packages read this file; "
            "neither retypes it. Regenerate with "
            "autocontext/scripts/generate_role_output_schemas.py."
        ),
        "schemas": {
            schema.name: schema.schema for schema in (ANALYST_SCHEMA, COACH_SCHEMA, ARCHITECT_SCHEMA)
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main() -> int:
    generated = build()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != generated:
            print(f"DRIFT: {OUT} differs from the pydantic models. Regenerate with:")
            print("  python autocontext/scripts/generate_role_output_schemas.py")
            return 1
        return 0
    OUT.write_text(generated, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
