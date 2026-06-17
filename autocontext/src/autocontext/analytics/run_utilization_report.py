"""Runner and token utilization report for parallel autoresearch runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class UtilizationWindow:
    started_at: str | None
    completed_at: str | None
    duration_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UtilizationWindow:
        _exact(data, {"started_at", "completed_at", "duration_seconds"})
        return cls(
            started_at=_nullable_string(data.get("started_at"), "started_at"),
            completed_at=_nullable_string(data.get("completed_at"), "completed_at"),
            duration_seconds=_nullable_number(data.get("duration_seconds"), "duration_seconds"),
        )


@dataclass(frozen=True)
class BranchUtilization:
    branch_count: int | None
    max_parallel_branches: int | None
    runner_capacity_seconds: float | None
    active_runner_seconds: float | None
    idle_runner_seconds: float | None
    mean_runner_utilization: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch_count": self.branch_count,
            "max_parallel_branches": self.max_parallel_branches,
            "runner_capacity_seconds": self.runner_capacity_seconds,
            "active_runner_seconds": self.active_runner_seconds,
            "idle_runner_seconds": self.idle_runner_seconds,
            "mean_runner_utilization": self.mean_runner_utilization,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BranchUtilization:
        _exact(
            data,
            {
                "branch_count",
                "max_parallel_branches",
                "runner_capacity_seconds",
                "active_runner_seconds",
                "idle_runner_seconds",
                "mean_runner_utilization",
            },
        )
        return cls(
            branch_count=_nullable_int(data.get("branch_count"), "branch_count"),
            max_parallel_branches=_nullable_int(data.get("max_parallel_branches"), "max_parallel_branches"),
            runner_capacity_seconds=_nullable_number(data.get("runner_capacity_seconds"), "runner_capacity_seconds"),
            active_runner_seconds=_nullable_number(data.get("active_runner_seconds"), "active_runner_seconds"),
            idle_runner_seconds=_nullable_number(data.get("idle_runner_seconds"), "idle_runner_seconds"),
            mean_runner_utilization=_nullable_number(data.get("mean_runner_utilization"), "mean_runner_utilization"),
        )


@dataclass(frozen=True)
class TokenUtilization:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    model_active_seconds: float | None
    model_wait_seconds: float | None
    mean_token_utilization: float | None
    token_throughput_per_second: float | None
    tokens_to_success: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "model_active_seconds": self.model_active_seconds,
            "model_wait_seconds": self.model_wait_seconds,
            "mean_token_utilization": self.mean_token_utilization,
            "token_throughput_per_second": self.token_throughput_per_second,
            "tokens_to_success": self.tokens_to_success,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenUtilization:
        _exact(
            data,
            {
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "model_active_seconds",
                "model_wait_seconds",
                "mean_token_utilization",
                "token_throughput_per_second",
                "tokens_to_success",
            },
        )
        return cls(
            input_tokens=_required_nonnegative_int(data.get("input_tokens"), "input_tokens"),
            output_tokens=_required_nonnegative_int(data.get("output_tokens"), "output_tokens"),
            total_tokens=_required_nonnegative_int(data.get("total_tokens"), "total_tokens"),
            model_active_seconds=_nullable_number(data.get("model_active_seconds"), "model_active_seconds"),
            model_wait_seconds=_nullable_number(data.get("model_wait_seconds"), "model_wait_seconds"),
            mean_token_utilization=_nullable_number(data.get("mean_token_utilization"), "mean_token_utilization"),
            token_throughput_per_second=_nullable_number(
                data.get("token_throughput_per_second"),
                "token_throughput_per_second",
            ),
            tokens_to_success=_nullable_nonnegative_int(data.get("tokens_to_success"), "tokens_to_success"),
        )


@dataclass(frozen=True)
class EvaluationUtilization:
    eval_count: int
    eval_active_seconds: float | None
    verifier_active_seconds: float | None
    verifier_idle_seconds: float | None
    eval_throughput_per_second: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eval_count": self.eval_count,
            "eval_active_seconds": self.eval_active_seconds,
            "verifier_active_seconds": self.verifier_active_seconds,
            "verifier_idle_seconds": self.verifier_idle_seconds,
            "eval_throughput_per_second": self.eval_throughput_per_second,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationUtilization:
        _exact(
            data,
            {
                "eval_count",
                "eval_active_seconds",
                "verifier_active_seconds",
                "verifier_idle_seconds",
                "eval_throughput_per_second",
            },
        )
        return cls(
            eval_count=_required_nonnegative_int(data.get("eval_count"), "eval_count"),
            eval_active_seconds=_nullable_number(data.get("eval_active_seconds"), "eval_active_seconds"),
            verifier_active_seconds=_nullable_number(data.get("verifier_active_seconds"), "verifier_active_seconds"),
            verifier_idle_seconds=_nullable_number(data.get("verifier_idle_seconds"), "verifier_idle_seconds"),
            eval_throughput_per_second=_nullable_number(
                data.get("eval_throughput_per_second"),
                "eval_throughput_per_second",
            ),
        )


@dataclass(frozen=True)
class RunUtilizationReport:
    run_id: str
    generated_at: str
    window: UtilizationWindow
    branch_utilization: BranchUtilization
    token_utilization: TokenUtilization
    evaluation_utilization: EvaluationUtilization

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "window": self.window.to_dict(),
            "branch_utilization": self.branch_utilization.to_dict(),
            "token_utilization": self.token_utilization.to_dict(),
            "evaluation_utilization": self.evaluation_utilization.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunUtilizationReport:
        _exact(
            data,
            {
                "schema_version",
                "run_id",
                "generated_at",
                "window",
                "branch_utilization",
                "token_utilization",
                "evaluation_utilization",
            },
        )
        if data.get("schema_version") != 1:
            raise ValueError("schema_version must be 1")
        return cls(
            run_id=_required_string(data.get("run_id"), "run_id"),
            generated_at=_required_string(data.get("generated_at"), "generated_at"),
            window=UtilizationWindow.from_dict(_record(data.get("window"), "window")),
            branch_utilization=BranchUtilization.from_dict(_record(data.get("branch_utilization"), "branch_utilization")),
            token_utilization=TokenUtilization.from_dict(_record(data.get("token_utilization"), "token_utilization")),
            evaluation_utilization=EvaluationUtilization.from_dict(
                _record(data.get("evaluation_utilization"), "evaluation_utilization")
            ),
        )


def build_run_utilization_report(
    *,
    run_id: str,
    events: list[dict[str, Any]],
    role_usage: list[dict[str, Any]],
    generated_at: str | None = None,
) -> RunUtilizationReport:
    normalized_events = [_normalize_event(event) for event in events]
    normalized_usage = [_normalize_usage(row) for row in role_usage]
    started_at, completed_at, duration = _window(normalized_events, normalized_usage)
    branches = _branch_ids(normalized_events, normalized_usage)
    max_parallel = _max_parallel_branches(normalized_events, completed_at) if branches else None
    capacity = _round(duration * max_parallel) if duration is not None and max_parallel else None
    eval_events = [event for event in normalized_events if event.get("event_type") in _EVAL_FINISHED]
    eval_active = _sum_duration(normalized_events, _EVAL_ACTIVE)
    explicit_runner_active = _sum_duration(normalized_events, _RUNNER_ACTIVE)
    active_runner = explicit_runner_active if explicit_runner_active is not None else eval_active
    verifier_active = _sum_duration(normalized_events, _VERIFIER_ACTIVE)
    input_tokens = sum(_int(row.get("input_tokens")) for row in normalized_usage)
    output_tokens = sum(_int(row.get("output_tokens")) for row in normalized_usage)
    total_tokens = input_tokens + output_tokens
    model_active = _sum_usage_seconds(normalized_usage, "latency_ms", scale=1000.0)
    model_wait = _sum_usage_seconds(normalized_usage, "model_wait_seconds")

    return RunUtilizationReport.from_dict(
        {
            "schema_version": 1,
            "run_id": run_id,
            "generated_at": generated_at or datetime.now().astimezone().isoformat(),
            "window": {
                "started_at": started_at,
                "completed_at": completed_at,
                "duration_seconds": duration,
            },
            "branch_utilization": {
                "branch_count": len(branches) if branches else None,
                "max_parallel_branches": max_parallel,
                "runner_capacity_seconds": capacity,
                "active_runner_seconds": active_runner,
                "idle_runner_seconds": _diff_or_none(capacity, active_runner),
                "mean_runner_utilization": _ratio(active_runner, capacity),
            },
            "token_utilization": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "model_active_seconds": model_active,
                "model_wait_seconds": model_wait,
                "mean_token_utilization": _ratio(model_active, capacity),
                "token_throughput_per_second": _ratio(total_tokens, model_active),
                "tokens_to_success": _tokens_to_success(normalized_events, normalized_usage),
            },
            "evaluation_utilization": {
                "eval_count": len(eval_events),
                "eval_active_seconds": eval_active,
                "verifier_active_seconds": verifier_active,
                "verifier_idle_seconds": _diff_or_none(capacity, verifier_active),
                "eval_throughput_per_second": _ratio(len(eval_events), duration),
            },
        }
    )


_RUNNER_ACTIVE = {"runner_active", "worker_active"}
_EVAL_FINISHED = {"evaluation_finished", "eval_finished"}
_EVAL_ACTIVE = {"evaluation_finished", "eval_finished", "evaluation_active"}
_VERIFIER_ACTIVE = {"verifier_finished", "verification_finished", "verifier_active"}


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    raw_payload = event.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    score_value = event.get("score") if event.get("score") is not None else payload.get("score")
    verifier_passed = event.get("verifier_passed")
    return {
        **event,
        "event_type": _string(event.get("event_type") or event.get("event")),
        "timestamp": _string(event.get("timestamp") or event.get("ts")),
        "branch_id": _string(event.get("branch_id") or payload.get("branch_id") or payload.get("worker_id")),
        "duration_seconds": _float_or_none(event.get("duration_seconds") or payload.get("duration_seconds")),
        "score": _float_or_none(score_value),
        "verifier_passed": verifier_passed if isinstance(verifier_passed, bool) else payload.get("verifier_passed"),
        "outcome": _string(event.get("outcome") or payload.get("outcome")),
    }


def _normalize_usage(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "timestamp": _string(row.get("timestamp") or row.get("ts")),
        "branch_id": _string(row.get("branch_id") or row.get("worker_id")),
        "input_tokens": _int(row.get("input_tokens")),
        "output_tokens": _int(row.get("output_tokens")),
        "latency_ms": _float_or_none(row.get("latency_ms")),
        "model_wait_seconds": _float_or_none(row.get("model_wait_seconds")),
    }


def _window(events: list[dict[str, Any]], usage: list[dict[str, Any]]) -> tuple[str | None, str | None, float | None]:
    values = [stamp for item in [*events, *usage] if (stamp := _parse_time(_string(item.get("timestamp"))))]
    if not values:
        return None, None, None
    start = min(values)
    end = max(values)
    return _format_time(start), _format_time(end), _round((end - start).total_seconds())


def _branch_ids(events: list[dict[str, Any]], usage: list[dict[str, Any]]) -> set[str]:
    return {branch for item in [*events, *usage] if (branch := _string(item.get("branch_id")))}


def _max_parallel_branches(events: list[dict[str, Any]], completed_at: str | None) -> int | None:
    starts: dict[str, datetime] = {}
    finishes: dict[str, datetime] = {}
    for event in events:
        branch = _string(event.get("branch_id"))
        timestamp = _parse_time(_string(event.get("timestamp")))
        if not branch or timestamp is None:
            continue
        if event.get("event_type") == "branch_started":
            starts[branch] = timestamp
        if event.get("event_type") == "branch_finished":
            finishes[branch] = timestamp
    fallback_end = _parse_time(completed_at or "")
    points: list[tuple[datetime, int]] = []
    for branch, start in starts.items():
        if start is None:
            continue
        end = finishes.get(branch) or fallback_end
        points.append((start, 1))
        if end is not None:
            points.append((end, -1))
    if not points:
        return len(_branch_ids(events, [])) or None
    current = 0
    max_seen = 0
    for _, delta in sorted(points, key=lambda item: (item[0], item[1])):
        current += delta
        max_seen = max(max_seen, current)
    return max_seen or None


def _sum_duration(events: list[dict[str, Any]], event_types: set[str]) -> float | None:
    values: list[float] = []
    for event in events:
        value = _float_or_none(event.get("duration_seconds"))
        if event.get("event_type") in event_types and value is not None:
            values.append(value)
    return _round(sum(values)) if values else None


def _sum_usage_seconds(rows: list[dict[str, Any]], key: str, *, scale: float = 1.0) -> float | None:
    values: list[float] = []
    for row in rows:
        value = _float_or_none(row.get(key))
        if value is not None:
            values.append(value)
    return _round(sum(values) / scale) if values else None


def _tokens_to_success(events: list[dict[str, Any]], usage: list[dict[str, Any]]) -> int | None:
    success_times: list[datetime] = []
    for event in events:
        timestamp = _parse_time(_string(event.get("timestamp")))
        if _is_success(event) and timestamp is not None:
            success_times.append(timestamp)
    if not success_times:
        return None
    first_success = min(success_times)
    total = 0
    for row in usage:
        timestamp = _parse_time(_string(row.get("timestamp")))
        if timestamp is not None and timestamp <= first_success:
            total += _int(row.get("input_tokens")) + _int(row.get("output_tokens"))
    return total


def _is_success(event: dict[str, Any]) -> bool:
    return event.get("verifier_passed") is True or _string(event.get("outcome")) == "success"


def _diff_or_none(left: float | None, right: float | None) -> float | None:
    return _round(max(0.0, left - right)) if left is not None and right is not None else None


def _ratio(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return _round(float(numerator) / float(denominator))


def _round(value: float) -> float:
    return round(value, 6)


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_time(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _record(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _exact(data: dict[str, Any], allowed: set[str]) -> None:
    missing = sorted(allowed - set(data))
    if missing:
        raise ValueError(f"missing field(s): {', '.join(missing)}")
    extra = sorted(set(data) - allowed)
    if extra:
        raise ValueError(f"unexpected field(s): {', '.join(extra)}")


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _nullable_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, label)


def _required_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _nullable_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _required_int(value, label)


def _required_nonnegative_int(value: Any, label: str) -> int:
    result = _required_int(value, label)
    if result < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return result


def _nullable_nonnegative_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _required_nonnegative_int(value, label)


def _nullable_number(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be a number or null")
    return float(value)


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


__all__ = [
    "BranchUtilization",
    "EvaluationUtilization",
    "RunUtilizationReport",
    "TokenUtilization",
    "UtilizationWindow",
    "build_run_utilization_report",
]
