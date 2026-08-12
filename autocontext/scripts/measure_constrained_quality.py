"""AC-928: does forcing a schema make the analysis worse?

AC-913 switched every OpenAI-compatible backend to constrained decoding. This
harness validates every candidate payload against the shipped analyst contract;
invalid trials are rejected rather than scored. The open question is whether
the analysis is as **substantive** when a model fills a schema instead of
writing prose -- a model that emits perfectly-shaped empty-calorie findings
steers the loop just as confidently as a good one.

Measured with objective proxies rather than an LLM judge, so the numbers are
reproducible and not hostage to a judge's mood or a second model's quirks:

* **count** -- how many findings / root causes / recommendations came back
* **substance** -- mean characters per item; one-word findings are the
  degenerate case a schema could invite
* **grounding** -- share of items citing a number that appears in the scenario
  (14 steps, 40 steps, 0.41, 2 of 5, 6 steps, 9-step median). An analysis that
  never touches the evidence is the failure mode worth catching
* **actionability** -- share of recommendations naming a parameter or a
  concrete quantity, which is what the prompt actually asks for

Usage:
    python scripts/measure_constrained_quality.py --base-url http://localhost:11434/v1 \
        --model llama3.1:8b --api-key ollama --trials 20
    python scripts/measure_constrained_quality.py --base-url https://api.anthropic.com/v1 \
        --model claude-sonnet-4-5 --api-key "$ANTHROPIC_API_KEY" --mode anthropic_tool --trials 20
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import re
import statistics
import sys
import urllib.request
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

ANALYST_TASK = "Analyze strengths/failures and return markdown with sections: Findings, Root Causes, Actionable Recommendations."

CONTEXT = """Scenario: grid_ctf, generation 3.
Competitor strategy: greedy nearest-flag with a 2-step lookahead, aggression=0.8.
Result: score 0.41 of 1.0. Captured 2 of 5 flags.
Failures: agent oscillated between two equidistant flags for 14 of 40 steps;
walked into a guarded tile twice; never used the decoy action.
Strengths: reached the first flag in 6 steps, below the 9-step median."""

# Numbers that appear in the scenario. An item citing one is engaging with the
# evidence rather than producing generic advice.
EVIDENCE = ("0.41", "2 of 5", "14", "40", "6 steps", "9", "0.8", "2-step")

# Parameter names the scenario names, plus any explicit quantity.
PARAM = re.compile(r"\b(aggression|lookahead|decoy|tiebreak|threshold|weight|step)\w*\b|\b\d+(\.\d+)?\b", re.I)


def _post_once(
    url: str,
    api_key: str,
    payload: dict[str, Any],
    deadline: float,
    sender: Connection,
    anthropic_api: bool,
) -> None:
    """Perform one request in a child process the parent can terminate."""
    try:
        headers = {"Content-Type": "application/json"}
        if anthropic_api:
            headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
        else:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=deadline) as resp:  # noqa: S310 - operator-supplied endpoint
            result = json.loads(resp.read())
        sender.send((True, result))
    except Exception as exc:  # noqa: BLE001 - serialize any transport failure for the parent
        sender.send((False, type(exc).__name__, str(exc)))
    finally:
        sender.close()


def _post(
    url: str,
    api_key: str,
    payload: dict[str, Any],
    *,
    attempts: int = 3,
    deadline: float = 120.0,
    anthropic_api: bool = False,
    _worker: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """POST with a bounded wait and a retry.

    urllib's ``timeout`` is per socket operation, not per request, so a gateway
    that dribbles bytes resets it on every chunk and holds the connection open
    indefinitely. Observed twice against OpenRouter: a run sat on one trial for
    18 minutes with a 120s socket timeout set, while ordinary calls to the same
    model returned in two seconds.

    The deadline is therefore enforced from a parent process. An overrun child
    is terminated and joined before retrying, so neither retries nor interpreter
    shutdown can accumulate hidden in-flight requests.
    """

    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if deadline <= 0:
        raise ValueError("deadline must be positive")

    last: Exception | None = None
    for attempt in range(attempts):
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_post_once if _worker is None else _worker,
            args=(url, api_key, payload, deadline, sender, anthropic_api),
            daemon=True,
        )
        process.start()
        sender.close()
        try:
            if not receiver.poll(deadline):
                raise TimeoutError(f"request exceeded {deadline:.3f}s deadline")
            message = receiver.recv()
            if message[0] is True:
                return message[1]
            raise RuntimeError(f"{message[1]}: {message[2]}")
        except Exception as exc:  # noqa: BLE001, PERF203 - any failure is a retry
            last = exc
            print(f"    retry {attempt + 1}/{attempts}: {type(exc).__name__}", flush=True)
        finally:
            receiver.close()
            if process.is_alive():
                process.terminate()
            process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
                process.join()
    raise RuntimeError(f"request failed after {attempts} attempts: {last}")


def _messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "You are the analyst agent in an optimization loop."},
        {"role": "user", "content": f"{CONTEXT}\n\n{ANALYST_TASK}"},
    ]


def _prose_items(markdown: str) -> dict[str, list[str]]:
    """Every bullet the model wrote, by ANY marker, regardless of heading level.

    Deliberately NOT the shipped scraper. The question here is whether the
    model's ANALYSIS degrades when it fills a schema, not whether the parser can
    read it -- that is AC-913 and already measured. Scoring the prose arm
    through the strict scraper conflates the two and, on a model that drifts,
    yields zero items and no comparison at all (observed: llama3.1:8b scores 0
    items on every prose trial, which says nothing about its reasoning).
    """
    items = [
        re.sub(r"^\s*([-*+]|\d+[.)])\s*", "", line).strip()
        for line in markdown.splitlines()
        if re.match(r"^\s*([-*+]|\d+[.)])\s+\S", line)
    ]
    # Recommendations are scored separately, so approximate the split by the
    # last heading that mentions recommendations; everything else is analysis.
    split = re.search(r"^#+\s*.*Recommendation", markdown, re.MULTILINE | re.IGNORECASE)
    if split:
        head, tail = markdown[: split.start()], markdown[split.start() :]
        recs = [
            re.sub(r"^\s*([-*+]|\d+[.)])\s*", "", line).strip()
            for line in tail.splitlines()
            if re.match(r"^\s*([-*+]|\d+[.)])\s+\S", line)
        ]
        others = [
            re.sub(r"^\s*([-*+]|\d+[.)])\s*", "", line).strip()
            for line in head.splitlines()
            if re.match(r"^\s*([-*+]|\d+[.)])\s+\S", line)
        ]
        return {"analysis": others, "recommendations": recs}
    return {"analysis": items, "recommendations": []}


def _unused_scrape(markdown: str) -> dict[str, list[str]]:
    """Kept for reference: the shipped scraper's exact rule."""
    out: dict[str, list[str]] = {}
    for key, heading in (
        ("findings", "Findings"),
        ("root_causes", "Root Causes"),
        ("recommendations", "Actionable Recommendations"),
    ):
        match = re.search(rf"^##\s+{re.escape(heading)}\s*$", markdown, re.MULTILINE)
        bullets: list[str] = []
        if match:
            for line in markdown[match.end() :].splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    break
                if stripped.startswith("- "):
                    bullets.append(stripped[2:].strip())
        out[key] = bullets
    return out


def _score(sections: dict[str, list[str]]) -> dict[str, float]:
    items = [i for group in sections.values() for i in group]
    recs = sections.get("recommendations", [])
    if not items:
        return {"items": 0, "mean_chars": 0.0, "grounded": 0.0, "actionable": 0.0}
    grounded = sum(1 for i in items if any(e in i for e in EVIDENCE))
    actionable = sum(1 for r in recs if PARAM.search(r))
    return {
        "items": len(items),
        "mean_chars": statistics.mean(len(i) for i in items),
        "grounded": grounded / len(items),
        "actionable": (actionable / len(recs)) if recs else 0.0,
    }


def _score_payload(payload: dict[str, Any]) -> dict[str, float]:
    """Validate the real analyst contract before a payload enters the score."""
    from autocontext.agents.role_schemas import AnalystPayload

    validated = AnalystPayload.model_validate(payload)
    return _score(validated.model_dump())


def _constrained_request(mode: str, base: dict[str, Any], schema: Any) -> dict[str, Any]:
    """Build the schema-constrained arm for the mechanism under test.

    Two mechanisms, because the providers differ in kind rather than in syntax:

    * ``response_format`` -- what AC-913 shipped for OpenAI-compatible backends.
    * ``tool`` -- a strict function tool through an OpenAI-compatible gateway.
    * ``anthropic_tool`` -- the native Anthropic Messages strict-tool shape.
      AC-928's open question is whether *being made to fill a schema* costs
      analysis quality, so the forced-tool arm has to be measured on its own
      rather than assumed to behave like ``response_format``.
    """
    if mode == "response_format":
        return {
            **base,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.name, "strict": True, "schema": schema.schema},
            },
        }
    from autocontext.providers.anthropic import _anthropic_strict_schema

    if mode == "anthropic_tool":
        return {
            **base,
            "tools": [
                {
                    "name": schema.name,
                    "description": "Return the analysis as structured data.",
                    "input_schema": _anthropic_strict_schema(schema.schema),
                    "strict": True,
                }
            ],
            "tool_choice": {"type": "tool", "name": schema.name},
        }
    return {
        **base,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": schema.name,
                    "description": "Return the analysis as structured data.",
                    "parameters": _anthropic_strict_schema(schema.schema),
                    "strict": True,
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": schema.name}},
    }


def _constrained_payload(
    mode: str,
    response: dict[str, Any],
    *,
    expected_tool: str = "analyst_output",
) -> dict[str, Any]:
    """Pull the object out of whichever channel the mechanism used."""
    if mode == "anthropic_tool":
        for block in response.get("content", []):
            if (
                block.get("type") == "tool_use"
                and block.get("name") == expected_tool
                and isinstance(block.get("input"), dict)
            ):
                return block["input"]
        raise json.JSONDecodeError("forced tool call produced no tool_use", "", 0)
    message = response["choices"][0]["message"]
    if mode == "response_format":
        return json.loads(message["content"])
    calls = message.get("tool_calls") or []
    if not calls or calls[0].get("function", {}).get("name") != expected_tool:
        raise json.JSONDecodeError("forced tool call produced no tool_calls", "", 0)
    return json.loads(calls[0]["function"]["arguments"])


def _prose_content(mode: str, response: dict[str, Any]) -> str:
    if mode == "anthropic_tool":
        for block in response.get("content", []):
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                return block["text"]
        raise KeyError("Anthropic response contained no text block")
    return response["choices"][0]["message"]["content"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key", default="no-key")
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--out", default="")
    ap.add_argument(
        "--mode",
        choices=("response_format", "tool", "anthropic_tool"),
        default="response_format",
        help=(
            "Constraint mechanism: response_format, an OpenAI-compatible strict tool, "
            "or a native Anthropic Messages strict tool"
        ),
    )
    args = ap.parse_args()

    from autocontext.agents.role_schemas import ANALYST_SCHEMA

    anthropic_api = args.mode == "anthropic_tool"
    resource = "messages" if anthropic_api else "chat/completions"
    url = f"{args.base_url.rstrip('/')}/{resource}"
    prose_scores: list[dict[str, float]] = []
    schema_scores: list[dict[str, float]] = []
    schema_rejected = 0

    for i in range(args.trials):
        messages = _messages()
        base: dict[str, Any] = {
            "model": args.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 900,
        }
        if anthropic_api:
            base["system"] = messages[0]["content"]
            base["messages"] = messages[1:]

        prose = _prose_content(args.mode, _post(url, args.api_key, base, anthropic_api=anthropic_api))
        prose_scores.append(_score(_prose_items(prose)))

        response = _post(
            url,
            args.api_key,
            _constrained_request(args.mode, base, ANALYST_SCHEMA),
            anthropic_api=anthropic_api,
        )
        try:
            schema_scores.append(_score_payload(_constrained_payload(args.mode, response)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            schema_rejected += 1
        print(f"  trial {i + 1}/{args.trials}", flush=True)

    def agg(rows: list[dict[str, float]]) -> dict[str, dict[str, float]]:
        """Mean plus spread, because a mean alone cannot support the decision.

        AC-928 asks whether constrained output is *worse*, and two means a few
        percent apart say nothing without knowing how much a single trial
        varies. Reporting sd and the standard error of the mean lets a reader
        check whether a gap is larger than the run-to-run wobble instead of
        taking the point estimate on faith.
        """
        if not rows:
            return {}
        out: dict[str, dict[str, float]] = {}
        for key in rows[0]:
            values = [r[key] for r in rows]
            sd = statistics.stdev(values) if len(values) > 1 else 0.0
            out[key] = {
                "mean": round(statistics.mean(values), 3),
                "sd": round(sd, 3),
                "sem": round(sd / (len(values) ** 0.5), 3) if values else 0.0,
            }
        return out

    verdict = {
        "model": args.model,
        "mode": args.mode,
        "trials": args.trials,
        "prose": agg(prose_scores),
        "schema": agg(schema_scores),
        "schema_rejected": schema_rejected,
        # Retained so a reader can re-aggregate or run a different test without
        # paying for the API calls again.
        "raw": {"prose": prose_scores, "schema": schema_scores},
    }
    print(json.dumps({k: v for k, v in verdict.items() if k != "raw"}, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
