from __future__ import annotations

import json
from pathlib import Path

from autocontext.ambient.sources.jsonl_feed import JsonlFeedSource


def _write(feed: Path, name: str, objs: list[dict]) -> None:
    feed.mkdir(parents=True, exist_ok=True)
    (feed / name).write_text("".join(json.dumps(o) + "\n" for o in objs), encoding="utf-8")


def test_reads_files_in_order_with_cursor(tmp_path: Path) -> None:
    feed = tmp_path / "feed"
    _write(feed, "a.jsonl", [{"kind": "llm_call", "n": 1}, {"n": 2}])
    _write(feed, "b.jsonl", [{"n": 3, "produced_by": "finetune:l1"}])
    source = JsonlFeedSource(name="otel", feed_dir=feed)
    first = source.poll(None)
    assert [r.payload["n"] for r in first.records] == [1, 2, 3]
    assert first.records[0].kind == "llm_call"
    assert first.records[1].kind == "trace"
    assert first.records[2].produced_by == "finetune:l1"
    assert first.next_cursor == "b.jsonl:1"
    again = source.poll(first.next_cursor)
    assert again.records == []
    assert again.next_cursor is None


def test_new_data_after_cursor_is_picked_up(tmp_path: Path) -> None:
    feed = tmp_path / "feed"
    _write(feed, "a.jsonl", [{"n": 1}])
    source = JsonlFeedSource(name="otel", feed_dir=feed)
    first = source.poll(None)
    _write(feed, "a.jsonl", [{"n": 1}, {"n": 2}])
    second = source.poll(first.next_cursor)
    assert [r.payload["n"] for r in second.records] == [2]


def test_malformed_lines_are_skipped_but_advance(tmp_path: Path) -> None:
    feed = tmp_path / "feed"
    feed.mkdir(parents=True)
    (feed / "a.jsonl").write_text('{"n": 1}\nnot json\n{"n": 3}\n', encoding="utf-8")
    source = JsonlFeedSource(name="otel", feed_dir=feed)
    result = source.poll(None)
    assert [r.payload["n"] for r in result.records] == [1, 3]
    assert result.next_cursor == "a.jsonl:3"


def test_unterminated_tail_is_held_until_complete(tmp_path: Path) -> None:
    feed = tmp_path / "feed"
    feed.mkdir(parents=True)
    (feed / "a.jsonl").write_text('{"n": 1}\n{"n": 2', encoding="utf-8")
    source = JsonlFeedSource(name="otel", feed_dir=feed)
    first = source.poll(None)
    assert [r.payload["n"] for r in first.records] == [1]
    assert first.next_cursor == "a.jsonl:1"
    with (feed / "a.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("3}\n")
    second = source.poll(first.next_cursor)
    assert [r.payload["n"] for r in second.records] == [23]
    assert second.next_cursor == "a.jsonl:2"


def test_batch_size_and_missing_dir(tmp_path: Path) -> None:
    source = JsonlFeedSource(name="otel", feed_dir=tmp_path / "absent")
    assert source.poll(None).records == []
    feed = tmp_path / "feed"
    _write(feed, "a.jsonl", [{"n": i} for i in range(5)])
    limited = JsonlFeedSource(name="otel", feed_dir=feed, batch_size=2)
    first = limited.poll(None)
    assert len(first.records) == 2
    second = limited.poll(first.next_cursor)
    assert [r.payload["n"] for r in second.records] == [2, 3]


def test_zero_batch_size_rejected(tmp_path: Path) -> None:
    import pytest

    source = JsonlFeedSource(name="otel", feed_dir=tmp_path, batch_size=0)
    with pytest.raises(ValueError, match="at least 1"):
        source.poll(None)
