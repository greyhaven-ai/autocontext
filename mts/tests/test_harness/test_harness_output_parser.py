"""Tests for mts.harness.core.output_parser — structured output extraction."""

from __future__ import annotations

import pytest

from mts.harness.core.output_parser import (
    extract_delimited_section,
    extract_json,
    extract_tagged_content,
    strip_json_fences,
)


class TestStripJsonFences:
    def test_strip_json_fences_with_json_tag(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        assert strip_json_fences(text) == '{"key": "value"}'

    def test_strip_json_fences_plain(self) -> None:
        text = '```\n{"key": "value"}\n```'
        assert strip_json_fences(text) == '{"key": "value"}'

    def test_strip_json_fences_no_fences(self) -> None:
        text = '{"key": "value"}'
        assert strip_json_fences(text) == '{"key": "value"}'


class TestExtractJson:
    def test_extract_json_valid(self) -> None:
        text = '```json\n{"name": "test", "count": 42}\n```'
        result = extract_json(text)
        assert result == {"name": "test", "count": 42}

    def test_extract_json_invalid(self) -> None:
        text = '```json\nnot valid json\n```'
        with pytest.raises(ValueError):
            extract_json(text)

    def test_extract_json_not_object(self) -> None:
        text = '```json\n[1, 2, 3]\n```'
        with pytest.raises(ValueError, match="Expected JSON object"):
            extract_json(text)


class TestExtractTaggedContent:
    def test_extract_tagged_content(self) -> None:
        text = "Before <code>print('hello')</code> after"
        assert extract_tagged_content(text, "code") == "print('hello')"

    def test_extract_tagged_content_missing(self) -> None:
        text = "No tags here"
        assert extract_tagged_content(text, "code") is None

    def test_extract_tagged_content_multiline(self) -> None:
        text = "<result>\nline 1\nline 2\nline 3\n</result>"
        result = extract_tagged_content(text, "result")
        assert result is not None
        assert "line 1" in result
        assert "line 3" in result


class TestExtractDelimitedSection:
    def test_extract_delimited_section(self) -> None:
        text = (
            "preamble\n"
            "<!-- PLAYBOOK_START -->\n"
            "Strategy content here\n"
            "<!-- PLAYBOOK_END -->\n"
            "postamble"
        )
        result = extract_delimited_section(text, "<!-- PLAYBOOK_START -->", "<!-- PLAYBOOK_END -->")
        assert result == "Strategy content here"

    def test_extract_delimited_section_missing(self) -> None:
        text = "No markers here"
        assert extract_delimited_section(text, "<!-- START -->", "<!-- END -->") is None
