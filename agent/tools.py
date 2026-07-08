import io
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime

from langchain_core.tools import BaseTool, tool

from .log import get_logger

logger = get_logger("tools")


@tool
def get_current_time() -> str:
    """Return the current local date and time."""
    logger.info("tool=get_current_time")
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


@tool
def get_current_location() -> str:
    """Return the current location (city, region, country) based on IP address."""
    logger.info("tool=get_current_location")
    try:
        with urllib.request.urlopen("http://ip-api.com/json", timeout=5) as resp:
            data = json.loads(resp.read())
        if data.get("status") == "success":
            return f"{data['city']}, {data['regionName']}, {data['country']}"
        return "Location unavailable"
    except Exception as e:
        return f"Location lookup failed: {e}"


@tool
def get_weather(location: str) -> str:
    """Get current weather for a location (city name or 'lat,lon' coordinates)."""
    logger.info("tool=get_weather location=%s", location)
    try:
        url = f"https://wttr.in/{urllib.parse.quote(location)}?format=j1"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        cur = data["current_condition"][0]
        desc = cur["weatherDesc"][0]["value"]
        return (
            f"{desc}, {cur['temp_C']}°C (feels like {cur['FeelsLikeC']}°C), "
            f"humidity {cur['humidity']}%, wind {cur['windspeedKmph']}km/h {cur['winddir16Point']}"
        )
    except Exception as e:
        return f"Weather lookup failed: {e}"


@tool
def read_file(path: str) -> str:
    """Read the contents of a file at the given path."""
    logger.info("tool=read_file path=%s", path)
    with open(path) as f:
        return f.read()


@tool
def write_file(path: str, content: str) -> str:
    """Write content to a file at the given path."""
    logger.info("tool=write_file path=%s chars=%d", path, len(content))
    with open(path, "w") as f:
        f.write(content)
    return f"Successfully written to {path}"


@tool
def python_repl(code: str) -> str:
    """Execute Python code and return stdout output or error message."""
    logger.info("tool=python_repl chars=%d", len(code))
    buf = io.StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = buf
        exec(compile(code, "<string>", "exec"), {})  # noqa: S102
    except Exception as e:
        buf.write(f"{type(e).__name__}: {e}")
    finally:
        sys.stdout = old_stdout
    return buf.getvalue()


def get_builtin_tools() -> list[BaseTool]:
    # 注意：网页搜索（Tavily）不在此处暴露为顶层工具，而是由 web-research skill
    # 独占（见 skills/web-research/search.py）。否则模型会绕过 skill 直接搜索，
    # 造成与 web-research 重复调用。
    return [
        python_repl,
        get_current_time,
        get_current_location,
        get_weather,
        read_file,
        write_file,
    ]
