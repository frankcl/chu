"""Tests for skills/web-research/search.py result normalization."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "web-research" / "search.py"


@pytest.fixture
def mod():
    spec = importlib.util.spec_from_file_location("web_research_search", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_search_one_requests_and_preserves_favicon(mod, monkeypatch):
    captured = {}

    class FakeTavilySearch:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def invoke(self, payload):
            assert payload == {"query": "query"}
            return {
                "results": [{
                    "title": "Title",
                    "url": "https://example.com/page",
                    "favicon": "https://example.com/icon.png",
                    "content": "body",
                }]
            }

    fake_module = types.SimpleNamespace(TavilySearch=FakeTavilySearch)
    monkeypatch.setitem(sys.modules, "langchain_tavily", fake_module)

    out = mod._search_one("query")

    assert captured["include_favicon"] is True
    assert out == {
        "query": "query",
        "results": [{
            "title": "Title",
            "url": "https://example.com/page",
            "favicon": "https://example.com/icon.png",
            "content": "body",
        }],
    }
