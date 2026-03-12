#!/usr/bin/env python3
"""Generate protocol artifacts from the server protocol source of truth.

This script:
1. Exports the JSON Schema from autocontext.server.protocol (the single source of truth)
2. Writes protocol/autocontext-protocol.json (committed, for cross-language validation)
3. Generates tui/src/protocol.generated.ts (Zod schemas derived from the JSON Schema)

Usage:
    python scripts/generate_protocol.py          # Generate all artifacts
    python scripts/generate_protocol.py --check   # Check parity only (CI mode)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Ensure the autocontext package is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
AUTOCONTEXT_SRC = REPO_ROOT / "autocontext" / "src"
sys.path.insert(0, str(AUTOCONTEXT_SRC))

from autocontext.server.protocol import export_json_schema  # noqa: E402


def _write_json_schema(schema: dict[str, Any], path: Path) -> None:
    """Write the JSON Schema file with consistent formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


def _json_schema_to_zod_type(prop: dict[str, Any], defs: dict[str, Any]) -> str:
    """Convert a JSON Schema property to a Zod type string."""
    def _apply_string_constraints(base: str) -> str:
        if "minLength" in prop:
            base += f".min({prop['minLength']})"
        if "maxLength" in prop:
            base += f".max({prop['maxLength']})"
        return base

    def _apply_numeric_constraints(base: str) -> str:
        if "exclusiveMinimum" in prop:
            base += f".gt({prop['exclusiveMinimum']})"
        elif "minimum" in prop:
            base += f".gte({prop['minimum']})"
        if "exclusiveMaximum" in prop:
            base += f".lt({prop['exclusiveMaximum']})"
        elif "maximum" in prop:
            base += f".lte({prop['maximum']})"
        return base

    if "$ref" in prop:
        ref_name = prop["$ref"].split("/")[-1]
        return f"{ref_name}Schema"

    if "const" in prop:
        val = prop["const"]
        if isinstance(val, str):
            return f'z.literal("{val}")'
        return f"z.literal({json.dumps(val)})"

    if "enum" in prop:
        values = prop["enum"]
        if all(isinstance(v, str) for v in values):
            quoted = ", ".join(f'"{v}"' for v in values)
            return f"z.enum([{quoted}])"
        return "z.union([" + ", ".join(f"z.literal({json.dumps(v)})" for v in values) + "])"

    if "anyOf" in prop:
        # Handle optional types (anyOf with null)
        non_null = [t for t in prop["anyOf"] if t.get("type") != "null"]
        has_null = len(non_null) < len(prop["anyOf"])
        if len(non_null) == 1:
            base = _json_schema_to_zod_type(non_null[0], defs)
            if has_null:
                return f"{base}.optional().nullable()"
            return base
        types = [_json_schema_to_zod_type(t, defs) for t in non_null]
        base = "z.union([" + ", ".join(types) + "])"
        if has_null:
            return f"{base}.optional().nullable()"
        return base

    schema_type = prop.get("type", "unknown")

    if schema_type == "string":
        return _apply_string_constraints("z.string()")
    if schema_type == "integer":
        return _apply_numeric_constraints("z.number().int()")
    if schema_type == "number":
        return _apply_numeric_constraints("z.number()")
    if schema_type == "boolean":
        return "z.boolean()"
    if schema_type == "null":
        return "z.null()"

    if schema_type == "array":
        items = prop.get("items", {})
        item_type = _json_schema_to_zod_type(items, defs)
        base = f"z.array({item_type})"
        if "minItems" in prop:
            base += f".min({prop['minItems']})"
        if "maxItems" in prop:
            base += f".max({prop['maxItems']})"
        return base

    if schema_type == "object":
        additional = prop.get("additionalProperties")
        if additional is True:
            # additionalProperties: true means any value type
            return "z.record(z.unknown())"
        if isinstance(additional, dict):
            val_type = _json_schema_to_zod_type(additional, defs)
            return f"z.record({val_type})"
        if "properties" in prop:
            # Inline object
            inner = _build_object_fields(prop, defs)
            return "z.object({" + ", ".join(f"{k}: {v}" for k, v in inner.items()) + "})"
        return "z.record(z.unknown())"

    return "z.unknown()"


def _build_object_fields(
    schema: dict[str, Any],
    defs: dict[str, Any],
    *,
    force_required: set[str] | None = None,
) -> dict[str, str]:
    """Build a dict of field_name -> zod_type for an object schema.

    Args:
        force_required: field names that should always be treated as required,
            even if they have defaults (e.g. ``type`` in discriminated unions).
    """
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    if force_required:
        required |= force_required
    fields: dict[str, str] = {}
    for name, prop in props.items():
        zod_type = _json_schema_to_zod_type(prop, defs)
        if name not in required:
            if ".optional()" not in zod_type:
                zod_type += ".optional()"
        fields[name] = zod_type
    return fields


def _generate_zod_schema(
    model_name: str,
    model_schema: dict[str, Any],
    defs: dict[str, Any],
    *,
    is_message: bool = False,
) -> str:
    """Generate a Zod schema definition for a single Pydantic model.

    Args:
        is_message: if True, force the ``type`` field to be required
            (needed for discriminated unions).
    """
    force_required = {"type"} if is_message else None
    fields = _build_object_fields(model_schema, defs, force_required=force_required)
    lines = [f"export const {model_name}Schema = z.object({{"]
    for field_name, zod_type in fields.items():
        lines.append(f"  {field_name}: {zod_type},")
    lines.append("});")
    return "\n".join(lines)


def _generate_discriminated_union(
    name: str,
    member_names: list[str],
) -> str:
    """Generate a Zod discriminatedUnion for ServerMessage / ClientMessage."""
    members = ", ".join(f"{n}Schema" for n in member_names)
    return f'export const {name}Schema = z.discriminatedUnion("type", [{members}]);'


def _extract_union_members(union_schema: dict[str, Any], defs: dict[str, Any]) -> list[str]:
    """Extract member model names from a discriminated union schema."""
    members: list[str] = []
    for variant in union_schema.get("anyOf", union_schema.get("oneOf", [])):
        if "$ref" in variant:
            ref_name = variant["$ref"].split("/")[-1]
            members.append(ref_name)
    return members


def _topo_sort_shared(names: list[str], defs: dict[str, Any]) -> list[str]:
    """Topologically sort shared model names so dependencies come first.

    A model A depends on model B if A has a $ref pointing to B.
    """
    name_set = set(names)
    deps: dict[str, set[str]] = {n: set() for n in names}

    def _collect_refs(obj: Any, target: set[str]) -> None:
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_name = obj["$ref"].split("/")[-1]
                if ref_name in name_set:
                    target.add(ref_name)
            for v in obj.values():
                _collect_refs(v, target)
        elif isinstance(obj, list):
            for item in obj:
                _collect_refs(item, target)

    for n in names:
        _collect_refs(defs[n], deps[n])

    # Kahn's algorithm
    in_degree = {n: 0 for n in names}
    for n, d in deps.items():
        for dep in d:
            if dep in in_degree:
                in_degree[n] += 1

    # Reverse: we want dependencies first
    graph: dict[str, set[str]] = {n: set() for n in names}
    for n, d in deps.items():
        for dep in d:
            if dep in graph:
                graph[dep].add(n)

    queue = [n for n in names if in_degree[n] == 0]
    result: list[str] = []
    while queue:
        queue.sort()  # deterministic ordering
        node = queue.pop(0)
        result.append(node)
        for dependent in sorted(graph[node]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # If there are cycles, append remaining
    remaining = [n for n in names if n not in result]
    result.extend(remaining)
    return result


def generate_typescript(schema: dict[str, Any]) -> str:
    """Generate TUI protocol TypeScript (Zod schemas) from the JSON Schema."""
    lines: list[str] = [
        "// AUTO-GENERATED from autocontext/src/autocontext/server/protocol.py",
        "// Do not edit manually. Run: python scripts/generate_protocol.py",
        "//",
        f"// Protocol version: {schema['protocol_version']}",
        "",
        'import { z } from "zod";',
        "",
    ]

    server_schema = schema["server_messages"]
    client_schema = schema["client_messages"]
    all_defs: dict[str, Any] = {}
    all_defs.update(server_schema.get("$defs", {}))
    all_defs.update(client_schema.get("$defs", {}))

    # Determine generation order: shared models first, then message models
    server_members = _extract_union_members(server_schema, all_defs)
    client_members = _extract_union_members(client_schema, all_defs)
    message_names = set(server_members) | set(client_members)
    shared_names = [n for n in all_defs if n not in message_names]

    # Topologically sort shared models so dependencies come first
    shared_names = _topo_sort_shared(shared_names, all_defs)

    # Generate shared/nested model schemas first
    for model_name in shared_names:
        model_schema_def = all_defs[model_name]
        lines.append(_generate_zod_schema(model_name, model_schema_def, all_defs))
        lines.append("")

    # Generate server message schemas
    lines.append("// --- Server -> Client messages ---")
    lines.append("")
    for model_name in server_members:
        if model_name in all_defs:
            lines.append(_generate_zod_schema(
                model_name, all_defs[model_name], all_defs, is_message=True,
            ))
            lines.append("")

    lines.append(_generate_discriminated_union("ServerMessage", server_members))
    lines.append("")

    # Generate client message schemas
    lines.append("// --- Client -> Server messages ---")
    lines.append("")
    for model_name in client_members:
        if model_name in all_defs:
            lines.append(_generate_zod_schema(
                model_name, all_defs[model_name], all_defs, is_message=True,
            ))
            lines.append("")

    lines.append(_generate_discriminated_union("ClientMessage", client_members))
    lines.append("")

    # Helper function
    lines.append("/** Parse a raw JSON string from the server into a typed message. Returns null on failure. */")
    lines.append("export function parseServerMessage(raw: string) {")
    lines.append("  try {")
    lines.append("    const json = JSON.parse(raw);")
    lines.append("    const result = ServerMessageSchema.safeParse(json);")
    lines.append("    return result.success ? result.data : null;")
    lines.append("  } catch {")
    lines.append("    return null;")
    lines.append("  }")
    lines.append("}")
    lines.append("")

    return "\n".join(lines)


def check_parity(schema: dict[str, Any]) -> bool:
    """Check that committed artifacts match the live schema. Returns True if in sync."""
    json_path = REPO_ROOT / "protocol" / "autocontext-protocol.json"
    ts_path = REPO_ROOT / "tui" / "src" / "protocol.generated.ts"

    ok = True

    # Check JSON schema
    if not json_path.exists():
        print(f"MISSING: {json_path}")
        ok = False
    else:
        committed = json.loads(json_path.read_text(encoding="utf-8"))
        if committed != schema:
            print(f"OUT OF DATE: {json_path}")
            ok = False

    # Check generated TypeScript
    if not ts_path.exists():
        print(f"MISSING: {ts_path}")
        ok = False
    else:
        expected_ts = generate_typescript(schema)
        actual_ts = ts_path.read_text(encoding="utf-8")
        if actual_ts != expected_ts:
            print(f"OUT OF DATE: {ts_path}")
            ok = False

    if ok:
        print("Protocol artifacts are in sync.")
    else:
        print("Run: python scripts/generate_protocol.py")

    return ok


def main() -> None:
    """Entry point."""
    schema = export_json_schema()

    if "--check" in sys.argv:
        if not check_parity(schema):
            sys.exit(1)
        return

    # Write JSON Schema
    json_path = REPO_ROOT / "protocol" / "autocontext-protocol.json"
    _write_json_schema(schema, json_path)
    print(f"Wrote {json_path}")

    # Write generated TypeScript
    ts_path = REPO_ROOT / "tui" / "src" / "protocol.generated.ts"
    ts_content = generate_typescript(schema)
    ts_path.write_text(ts_content, encoding="utf-8")
    print(f"Wrote {ts_path}")

    print("Protocol artifacts generated successfully.")


if __name__ == "__main__":
    main()
