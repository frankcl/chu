"""Tests for skills/ppt/search_image.py — Tavily image-result parsing.

Loaded by path (it is a standalone CLI script, like build.py). Only the pure
`_extract_images` parser is unit-tested; the live Tavily call needs network.
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "ppt" / "search_image.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("ppt_search_image", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestExtractImages:
    def test_url_string_list(self, mod):
        raw = {"images": ["https://a/1.png", "https://b/2.jpg"]}
        assert mod._extract_images(raw) == [
            {"url": "https://a/1.png", "description": ""},
            {"url": "https://b/2.jpg", "description": ""},
        ]

    def test_object_list_with_descriptions(self, mod):
        raw = {"images": [{"url": "https://a/1.png", "description": "a chart"}]}
        out = mod._extract_images(raw)
        assert out == [{"url": "https://a/1.png", "description": "a chart"}]

    def test_blank_and_malformed_entries_skipped(self, mod):
        raw = {"images": ["  ", {"url": ""}, {"no_url": "x"}, 123,
                          {"url": "https://ok/3.png"}]}
        assert mod._extract_images(raw) == [
            {"url": "https://ok/3.png", "description": ""},
        ]

    def test_description_truncated(self, mod):
        raw = {"images": [{"url": "https://a/1.png", "description": "x" * 500}]}
        assert len(mod._extract_images(raw)[0]["description"]) == 200

    def test_missing_images_key(self, mod):
        assert mod._extract_images({"results": []}) == []

    def test_non_dict_input(self, mod):
        assert mod._extract_images(None) == []
        assert mod._extract_images([1, 2, 3]) == []
