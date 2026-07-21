from agent.source_meta import extract_source_favicons


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
