"""Tests for agent/tools.py — tool functions with mocked HTTP."""

import json
import pytest
from unittest.mock import MagicMock, patch


# ── helpers ──────────────────────────────────────────────────────────────────

def _fake_urlopen(response_data: dict):
    """Return a context-manager mock that yields JSON bytes."""
    body = json.dumps(response_data).encode()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.read = MagicMock(return_value=body)
    return cm


# ── get_current_time ─────────────────────────────────────────────────────────

class TestGetCurrentTime:
    def test_returns_current_formatted_time(self):
        from datetime import datetime
        import re

        from agent.tools import get_current_time

        result = get_current_time.invoke({})
        assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", result)
        assert datetime.now().strftime("%Y-%m-%d") in result


# ── get_current_location ─────────────────────────────────────────────────────

class TestGetCurrentLocation:
    def test_success(self):
        from agent.tools import get_current_location
        fake_resp = {"status": "success", "city": "Beijing", "regionName": "Beijing", "country": "China"}
        with patch("agent.tools.urllib.request.urlopen", return_value=_fake_urlopen(fake_resp)):
            result = get_current_location.invoke({})
        assert result == "Beijing, Beijing, China"

    def test_api_failure_status(self):
        from agent.tools import get_current_location
        fake_resp = {"status": "fail", "message": "private range"}
        with patch("agent.tools.urllib.request.urlopen", return_value=_fake_urlopen(fake_resp)):
            result = get_current_location.invoke({})
        assert result == "Location unavailable"

    def test_network_error(self):
        from agent.tools import get_current_location
        with patch("agent.tools.urllib.request.urlopen", side_effect=OSError("timeout")):
            result = get_current_location.invoke({})
        assert result.startswith("Location lookup failed:")


# ── get_weather ───────────────────────────────────────────────────────────────

class TestGetWeather:
    _WEATHER_PAYLOAD = {
        "current_condition": [{
            "weatherDesc": [{"value": "Sunny"}],
            "temp_C": "22",
            "FeelsLikeC": "21",
            "humidity": "40",
            "windspeedKmph": "10",
            "winddir16Point": "NW",
        }]
    }

    def test_success(self):
        from agent.tools import get_weather
        with patch("agent.tools.urllib.request.urlopen", return_value=_fake_urlopen(self._WEATHER_PAYLOAD)):
            result = get_weather.invoke({"location": "Beijing"})
        assert "Sunny" in result
        assert "22°C" in result
        assert "humidity 40%" in result

    def test_location_is_url_encoded(self):
        """Spaces in location name should be URL-encoded in the request URL."""
        from agent.tools import get_weather
        captured_url = []

        def fake_open(url, timeout=None):
            captured_url.append(url)
            return _fake_urlopen(self._WEATHER_PAYLOAD)

        with patch("agent.tools.urllib.request.urlopen", side_effect=fake_open):
            get_weather.invoke({"location": "New York"})

        assert "New%20York" in captured_url[0]

    def test_network_error(self):
        from agent.tools import get_weather
        with patch("agent.tools.urllib.request.urlopen", side_effect=OSError("timeout")):
            result = get_weather.invoke({"location": "X"})
        assert result.startswith("Weather lookup failed:")


# ── read_file / write_file ────────────────────────────────────────────────────

class TestReadFile:
    def test_reads_existing_file(self, tmp_path):
        from agent.tools import read_file
        f = tmp_path / "hello.txt"
        f.write_text("hello world")
        result = read_file.invoke({"path": str(f)})
        assert result == "hello world"

    def test_missing_file_raises(self, tmp_path):
        from agent.tools import read_file
        with pytest.raises(FileNotFoundError):
            read_file.invoke({"path": str(tmp_path / "missing.txt")})


class TestWriteFile:
    def test_creates_file_and_reports_path(self, tmp_path):
        from agent.tools import write_file

        path = str(tmp_path / "out.txt")
        result = write_file.invoke({"path": path, "content": "data"})
        assert "Successfully written" in result
        assert path in result
        assert (tmp_path / "out.txt").read_text() == "data"

    def test_overwrites_existing_file(self, tmp_path):
        from agent.tools import write_file
        f = tmp_path / "out.txt"
        f.write_text("old content")
        write_file.invoke({"path": str(f), "content": "new content"})
        assert f.read_text() == "new content"

# ── get_builtin_tools ─────────────────────────────────────────────────────────

class TestGetBuiltinTools:
    def test_returns_expected_described_tools(self):
        from agent.tools import get_builtin_tools

        tools = get_builtin_tools()
        assert isinstance(tools, list)
        assert {
            "get_current_time",
            "get_current_location",
            "get_weather",
            "read_file",
            "write_file",
        } <= {tool.name for tool in tools}
        for t in tools:
            assert t.description, f"Tool '{t.name}' has no description"
