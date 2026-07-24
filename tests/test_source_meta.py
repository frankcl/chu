from agent.source_meta import extract_source_favicons, source_favicons_for_tool


def test_extract_source_favicons_from_json():
    raw = '{"results":[{"url":"https://example.com/a","favicon":"https://cdn.example.com/icon.png"}]}'
    assert extract_source_favicons(raw) == [{
        "url": "https://example.com/a",
        "favicon": "https://cdn.example.com/icon.png",
    }]


def test_extract_source_favicons_from_truncated_text():
    raw = '"url": "https://example.com/a", "favicon": "https://cdn.example.com/icon.png", "content": "'
    assert extract_source_favicons(raw) == [{
        "url": "https://example.com/a",
        "favicon": "https://cdn.example.com/icon.png",
    }]


def test_source_favicons_for_web_research_search_script():
    raw = '{"results":[{"url":"https://example.com/a","favicon":"https://cdn.example.com/icon.png"}]}'
    tool_input = '{"skill":"web-research","script":"search.py","script_args":["q"]}'

    assert source_favicons_for_tool("run_skill_script", raw, tool_input) == [{
        "url": "https://example.com/a",
        "favicon": "https://cdn.example.com/icon.png",
    }]


def test_source_favicons_for_other_skill_script_is_empty():
    raw = '{"results":[{"url":"https://example.com/a","favicon":"https://cdn.example.com/icon.png"}]}'
    tool_input = '{"skill":"ppt","script":"search_image.py","script_args":["q"]}'

    assert source_favicons_for_tool("run_skill_script", raw, tool_input) == []


def test_source_favicons_for_non_search_tool_is_empty():
    raw = '{"url":"https://example.com/a","favicon":"https://cdn.example.com/icon.png"}'

    assert source_favicons_for_tool("get_weather", raw) == []
