def choose_action(state):
    if not isinstance(state, dict) or set(state) != {"schema_version", "name", "enabled"}:
        return {"abstain": True}
    if type(state["schema_version"]) is not int or state["schema_version"] != 1:
        return {"abstain": True}
    if type(state["name"]) is not str or not 1 <= len(state["name"]) <= 1024:
        return {"abstain": True}
    if type(state["enabled"]) is not bool:
        return {"abstain": True}
    return {"schema_version": 2, "display_name": state["name"],
            "status": "enabled" if state["enabled"] else "disabled"}
