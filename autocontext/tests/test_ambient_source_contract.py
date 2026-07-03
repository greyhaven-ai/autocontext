from __future__ import annotations

from dataclasses import dataclass

from autocontext.ambient.sources.contract import RawTrace, SourcePoll, TraceSource


@dataclass
class FakeSource:
    name: str

    def poll(self, cursor: str | None) -> SourcePoll:
        return SourcePoll(records=[RawTrace(kind="x", payload={"cursor": cursor})], next_cursor="1")


def test_contract_shapes() -> None:
    source: TraceSource = FakeSource(name="fake")
    result = source.poll(None)
    assert result.records[0].payload == {"cursor": None}
    assert result.records[0].produced_by == "frontier"
    assert result.next_cursor == "1"
