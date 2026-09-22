# Hand-authored integration fixture; not a learned efficacy candidate.
def choose_action(state):
    return {"schema_version": 2, "display_name": state["name"],
            "status": "enabled" if state["enabled"] else "disabled"}
