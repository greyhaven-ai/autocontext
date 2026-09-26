def choose_action(state):
    try:
        import json
        data = json.loads(state) if isinstance(state, str) else state
    except Exception:
        return {"abstain": True}

    if not isinstance(data, dict):
        return {"abstain": True}

    if set(data.keys()) != {"schema_version", "name", "enabled"}:
        return {"abstain": True}

    if data.get("schema_version") != 1:
        return {"abstain": True}

    name = data.get("name")
    if not isinstance(name, str) or not name:
        return {"abstain": True}

    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return {"abstain": True}

    return {
        "schema_version": 2,
        "display_name": name,
        "status": "enabled" if enabled else "disabled"
    }
