from autocontext.agents.architect import parse_architect_tool_specs


def test_parse_architect_tool_specs_extracts_valid_entries() -> None:
    content = """
## Observed Bottlenecks

- Missing risk analysis helper.

```json
{
  "tools": [
    {
      "name": "risk_helper",
      "description": "Compute risk.",
      "code": "def run(inputs):\\n    return {\\"risk\\": 0.2}"
    }
  ]
}
```
"""
    tools = parse_architect_tool_specs(content)
    assert len(tools) == 1
    assert tools[0]["name"] == "risk_helper"
