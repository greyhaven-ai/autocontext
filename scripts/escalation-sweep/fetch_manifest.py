#!/usr/bin/env python3
"""Fetch Linear Scenarios-state issues and emit a sweep manifest.

Reads the Linear personal API key from ~/.config/linear/credentials.toml
(the `greyhaven` key). Writes a JSON manifest of {identifier, title, body}
entries to the path given on the command line.

Usage:
    python fetch_manifest.py <output_path>
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

LINEAR_API = "https://api.linear.app/graphql"
SCENARIOS_STATE_ID = "828fd036-0d14-4dc3-9af1-28a72977f33b"
CREDENTIALS_PATH = Path.home() / ".config" / "linear" / "credentials.toml"


def _load_key() -> str:
    env_key = os.environ.get("LINEAR_API_KEY")
    if env_key:
        return env_key
    if not CREDENTIALS_PATH.exists():
        raise SystemExit(
            f"no LINEAR_API_KEY env var and {CREDENTIALS_PATH} not found"
        )
    for line in CREDENTIALS_PATH.read_text().splitlines():
        line = line.strip()
        if line.startswith("greyhaven"):
            _, _, value = line.partition("=")
            return value.strip().strip('"')
    raise SystemExit("no 'greyhaven' entry in credentials.toml")


def _gql(key: str, query: str, variables: dict | None = None) -> dict:
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        LINEAR_API,
        data=payload,
        headers={"Authorization": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())
    if "errors" in body:
        raise SystemExit(f"Linear API error: {body['errors']}")
    return body["data"]


def fetch_scenarios(key: str) -> list[dict]:
    query = """
    query Scenarios($stateId: ID!, $after: String) {
      issues(
        filter: { state: { id: { eq: $stateId } } }
        first: 50
        after: $after
      ) {
        nodes { identifier title description }
        pageInfo { hasNextPage endCursor }
      }
    }
    """
    nodes: list[dict] = []
    cursor: str | None = None
    while True:
        data = _gql(
            key,
            query,
            {"stateId": SCENARIOS_STATE_ID, "after": cursor},
        )
        issues = data["issues"]
        nodes.extend(issues["nodes"])
        if not issues["pageInfo"]["hasNextPage"]:
            break
        cursor = issues["pageInfo"]["endCursor"]
    return nodes


def to_manifest_entry(issue: dict) -> dict:
    # Use title + body for the solve description; body may be multi-section.
    title = issue.get("title", "").strip()
    body = (issue.get("description") or "").strip()
    description = f"# {title}\n\n{body}" if body else title
    return {
        "identifier": issue["identifier"],
        "title": title,
        "description": description,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    output_path = Path(argv[1])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    key = _load_key()
    issues = fetch_scenarios(key)
    entries = [to_manifest_entry(issue) for issue in issues]
    output_path.write_text(json.dumps(entries, indent=2))
    print(f"wrote {len(entries)} entries to {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
