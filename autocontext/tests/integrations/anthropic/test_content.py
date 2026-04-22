"""Tests for content-block flattening helpers (TDD — RED phase first)."""
from __future__ import annotations

import pytest

from autocontext.integrations.anthropic._content import (
    extract_tool_uses,
    flatten_content,
)


def test_string_content_passthrough() -> None:
    """String content is returned as-is."""
    assert flatten_content("hello world") == "hello world"


def test_empty_list_content_flattens_to_empty_string() -> None:
    """Empty list produces empty string."""
    assert flatten_content([]) == ""


def test_single_text_block_flattens_to_text() -> None:
    """A single text block returns just its text."""
    blocks = [{"type": "text", "text": "hello"}]
    assert flatten_content(blocks) == "hello"


def test_multiple_text_blocks_concatenated_in_order() -> None:
    """Multiple text blocks are joined in order with no separator."""
    blocks = [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": " "},
        {"type": "text", "text": "world"},
    ]
    assert flatten_content(blocks) == "hello world"


def test_image_blocks_dropped_from_content() -> None:
    """Image blocks are silently dropped."""
    blocks = [
        {"type": "text", "text": "before"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}},
        {"type": "text", "text": "after"},
    ]
    assert flatten_content(blocks) == "beforeafter"


def test_tool_use_blocks_dropped_from_content() -> None:
    """Tool-use blocks are silently dropped (only text accumulates)."""
    blocks = [
        {"type": "text", "text": "thinking..."},
        {"type": "tool_use", "id": "tu_1", "name": "get_weather", "input": {"city": "NYC"}},
    ]
    assert flatten_content(blocks) == "thinking..."


def test_tool_result_blocks_dropped_from_content() -> None:
    """Tool-result blocks are silently dropped."""
    blocks = [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "70°F"},
        {"type": "text", "text": "The weather is nice."},
    ]
    assert flatten_content(blocks) == "The weather is nice."


def test_extract_tool_uses_from_response_content() -> None:
    """tool_use blocks are extracted with toolName and args."""
    blocks = [
        {"type": "text", "text": "Let me check the weather."},
        {
            "type": "tool_use",
            "id": "tu_1",
            "name": "get_weather",
            "input": {"city": "NYC", "units": "celsius"},
        },
    ]
    result = extract_tool_uses(blocks)
    assert result == [{"toolName": "get_weather", "args": {"city": "NYC", "units": "celsius"}}]


def test_extract_tool_uses_from_string_returns_none() -> None:
    """String content (no blocks) returns None."""
    assert extract_tool_uses("hello") is None


def test_extract_tool_uses_empty_when_no_tool_use_blocks() -> None:
    """List with only text blocks returns None (not empty list)."""
    blocks = [{"type": "text", "text": "no tools here"}]
    assert extract_tool_uses(blocks) is None
