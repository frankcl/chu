"""Tests for agent/skills.py — frontmatter parsing, registry, overview, skill tools."""

from pathlib import Path
from unittest.mock import MagicMock

from agent.skills import (
    SkillRegistry,
    _parse_frontmatter,
    build_skill_tools,
    skills_overview,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_skill(root: Path, name: str, description: str, body: str = "do the thing",
                extra_files: dict[str, str] | None = None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    for rel, content in (extra_files or {}).items():
        (d / rel).write_text(content, encoding="utf-8")
    return d


# ── _parse_frontmatter ───────────────────────────────────────────────────────

class TestParseFrontmatter:
    def test_normal(self):
        meta, body = _parse_frontmatter("---\nname: x\ndescription: y\n---\n\nhello")
        assert meta == {"name": "x", "description": "y"}
        assert body == "hello"

    def test_no_frontmatter(self):
        meta, body = _parse_frontmatter("just text\nmore")
        assert meta == {}
        assert body == "just text\nmore"

    def test_malformed_yaml_returns_empty_meta(self):
        meta, body = _parse_frontmatter("---\n: : bad: yaml: [\n---\nbody")
        assert meta == {}

    def test_non_dict_frontmatter(self):
        meta, body = _parse_frontmatter("---\n- a\n- b\n---\nbody")
        assert meta == {}

    def test_unterminated_frontmatter(self):
        text = "---\nname: x\nno closing fence"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text


# ── SkillRegistry ─────────────────────────────────────────────────────────────

class TestSkillRegistry:
    def test_loads_multiple_skills(self, tmp_path):
        _make_skill(tmp_path, "alpha", "first skill")
        _make_skill(tmp_path, "beta", "second skill", extra_files={"run.py": "print(1)"})
        reg = SkillRegistry(tmp_path)
        names = sorted(s.name for s in reg.list())
        assert names == ["alpha", "beta"]
        assert reg.get("beta").files == ["run.py"]
        assert reg.get("missing") is None
        assert not reg.is_empty()

    def test_skips_missing_skill_md(self, tmp_path):
        (tmp_path / "empty_dir").mkdir()
        _make_skill(tmp_path, "ok", "valid")
        reg = SkillRegistry(tmp_path)
        assert [s.name for s in reg.list()] == ["ok"]

    def test_skips_skill_missing_required_fields(self, tmp_path):
        d = tmp_path / "broken"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: broken\n---\nno description", encoding="utf-8")
        reg = SkillRegistry(tmp_path)
        assert reg.is_empty()

    def test_nonexistent_dir_is_empty(self, tmp_path):
        reg = SkillRegistry(tmp_path / "does-not-exist")
        assert reg.is_empty()
        assert reg.list() == []

    def test_none_dir_is_empty(self):
        reg = SkillRegistry(None)
        assert reg.is_empty()
        assert reg.root is None


# ── SkillRegistry.resolve ─────────────────────────────────────────────────────

class TestResolve:
    def test_false_disables(self):
        assert SkillRegistry.resolve(False).is_empty()

    def test_passthrough_registry(self, tmp_path):
        reg = SkillRegistry(tmp_path)
        assert SkillRegistry.resolve(reg) is reg

    def test_str_and_path(self, tmp_path):
        _make_skill(tmp_path, "alpha", "d")
        assert not SkillRegistry.resolve(str(tmp_path)).is_empty()
        assert not SkillRegistry.resolve(tmp_path).is_empty()

    def test_env_override(self, tmp_path, monkeypatch):
        _make_skill(tmp_path, "envskill", "from env")
        monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
        reg = SkillRegistry.resolve(None)
        assert reg.get("envskill") is not None

    def test_default_dir_loads_repo_skills(self, monkeypatch):
        """With no override, the bundled repo skills/ directory is discovered."""
        monkeypatch.delenv("SKILLS_DIR", raising=False)
        reg = SkillRegistry.resolve(None)
        names = {s.name for s in reg.list()}
        assert {"text-stats", "web-research"} <= names


# ── skills_overview ────────────────────────────────────────────────────────────

class TestSkillsOverview:
    def test_empty_registry_returns_blank(self):
        assert skills_overview(SkillRegistry(None)) == ""

    def test_lists_names_and_descriptions(self, tmp_path):
        _make_skill(tmp_path, "alpha", "does alpha things")
        _make_skill(tmp_path, "beta", "does beta things")
        out = skills_overview(SkillRegistry(tmp_path))
        assert "## Available Skills" in out
        assert "alpha: does alpha things" in out
        assert "beta: does beta things" in out


# ── build_skill_tools ──────────────────────────────────────────────────────────

class TestSkillTool:
    def _tools(self, tmp_path):
        _make_skill(tmp_path, "alpha", "alpha desc", body="follow these steps",
                    extra_files={"helper.py": "print('hi')"})
        reg = SkillRegistry(tmp_path)
        tools = {t.name: t for t in build_skill_tools(reg, per_tool_timeout=5.0)}
        return reg, tools

    def test_empty_registry_no_tools(self):
        assert build_skill_tools(SkillRegistry(None), 5.0) == []

    def test_each_skill_exposed_as_named_tool(self, tmp_path):
        """Every skill becomes a tool named after the skill, plus run_skill_script."""
        _, tools = self._tools(tmp_path)
        assert set(tools) == {"alpha", "run_skill_script"}

    def test_skill_tool_description_uses_skill_description(self, tmp_path):
        _, tools = self._tools(tmp_path)
        assert "alpha desc" in tools["alpha"].description

    def test_invoking_skill_returns_instructions_path_and_files(self, tmp_path):
        reg, tools = self._tools(tmp_path)
        out = tools["alpha"].invoke({})
        assert "follow these steps" in out
        assert str(reg.get("alpha").path) in out
        assert "helper.py" in out

    def test_invalid_skill_name_not_exposed_as_tool(self, tmp_path):
        _make_skill(tmp_path, "good", "ok desc")
        # name with a space is invalid as a tool name
        d = tmp_path / "spaced"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: bad name\ndescription: nope\n---\nbody", encoding="utf-8"
        )
        reg = SkillRegistry(tmp_path)
        names = {t.name for t in build_skill_tools(reg, 5.0)}
        assert "good" in names
        assert "bad name" not in names


# ── run_skill_script ───────────────────────────────────────────────────────────

class TestRunSkillScript:
    def _tools(self, tmp_path, script_body: str):
        _make_skill(tmp_path, "runner", "runs scripts",
                    extra_files={"go.py": script_body})
        reg = SkillRegistry(tmp_path)
        tools = {t.name: t for t in build_skill_tools(reg, per_tool_timeout=10.0)}
        return reg, tools["run_skill_script"]

    _ECHO = "import sys; print('|'.join(sys.argv[1:]))"

    def test_runs_and_returns_stdout(self, tmp_path):
        _, run = self._tools(tmp_path, "import sys; print('hello ' + (sys.argv[1] if len(sys.argv)>1 else ''))")
        out = run.invoke({"skill": "runner", "script": "go.py", "script_args": ["world"]})
        assert "exit_code=0" in out
        assert "hello world" in out

    def test_script_args_as_string_is_coerced(self, tmp_path):
        """LLMs often pass a single arg as a bare string instead of a list."""
        _, run = self._tools(tmp_path, self._ECHO)
        out = run.invoke({"skill": "runner", "script": "go.py", "script_args": "solo arg"})
        assert "exit_code=0" in out
        assert "solo arg" in out

    def test_script_args_list_preserved(self, tmp_path):
        _, run = self._tools(tmp_path, self._ECHO)
        out = run.invoke({"skill": "runner", "script": "go.py", "script_args": ["a", "b"]})
        assert "a|b" in out

    def test_script_args_omitted(self, tmp_path):
        _, run = self._tools(tmp_path, self._ECHO)
        out = run.invoke({"skill": "runner", "script": "go.py"})
        assert "exit_code=0" in out

    def test_unknown_skill(self, tmp_path):
        _, run = self._tools(tmp_path, "print(1)")
        assert "Unknown skill" in run.invoke({"skill": "ghost", "script": "go.py"})

    def test_missing_script(self, tmp_path):
        _, run = self._tools(tmp_path, "print(1)")
        out = run.invoke({"skill": "runner", "script": "absent.py"})
        assert "not found" in out

    def test_path_traversal_relative_refused(self, tmp_path):
        # plant a script OUTSIDE the skill dir
        (tmp_path / "evil.py").write_text("print('pwned')", encoding="utf-8")
        _, run = self._tools(tmp_path, "print(1)")
        out = run.invoke({"skill": "runner", "script": "../evil.py"})
        assert "escapes the skill directory" in out

    def test_path_traversal_absolute_refused(self, tmp_path):
        _, run = self._tools(tmp_path, "print(1)")
        out = run.invoke({"skill": "runner", "script": "/etc/hosts"})
        assert "escapes the skill directory" in out

    def test_timeout(self, tmp_path):
        _make_skill(tmp_path, "slow", "slow", extra_files={"sleep.py": "import time; time.sleep(5)"})
        reg = SkillRegistry(tmp_path)
        run = {t.name: t for t in build_skill_tools(reg, per_tool_timeout=0.3)}["run_skill_script"]
        out = run.invoke({"skill": "slow", "script": "sleep.py"})
        assert "timeout" in out

    def test_script_cap_is_per_plan_task(self, tmp_path):
        _, run = self._tools(tmp_path, "print('ok')")
        counts = {}
        cfg_a = {
            "configurable": {
                "skill_call_counts": counts,
                "plan_task_id": "task-a",
                "max_skill_script_calls_per_task": 1,
            },
        }
        cfg_b = {
            "configurable": {
                "skill_call_counts": counts,
                "plan_task_id": "task-b",
                "max_skill_script_calls_per_task": 1,
            },
        }

        assert "exit_code=0" in run.invoke({"skill": "runner", "script": "go.py"}, config=cfg_a)
        assert "call cap reached" in run.invoke({"skill": "runner", "script": "go.py"}, config=cfg_a)
        assert "exit_code=0" in run.invoke({"skill": "runner", "script": "go.py"}, config=cfg_b)


# ── integration with ReActAgent ─────────────────────────────────────────────────

class TestReActAgentSkillWiring:
    def _mock_llm(self):
        mock_llm = MagicMock()
        mock_bound = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound
        mock_bound.with_retry.return_value = mock_bound
        return mock_llm

    def test_skill_tools_bound_when_registry_present(self, tmp_path, mocker):
        _make_skill(tmp_path, "alpha", "alpha desc")
        mock_llm = self._mock_llm()
        mocker.patch("agent.llm.LLM.chat_model", return_value=mock_llm)
        from agent import ReActAgent
        ReActAgent(skills=SkillRegistry(tmp_path))
        names = [t.name for t in mock_llm.bind_tools.call_args[0][0]]
        assert "alpha" in names and "run_skill_script" in names

    def test_no_skill_tools_when_disabled(self, mocker):
        mock_llm = self._mock_llm()
        mocker.patch("agent.llm.LLM.chat_model", return_value=mock_llm)
        from agent import ReActAgent
        ReActAgent(skills=False)
        names = [t.name for t in mock_llm.bind_tools.call_args[0][0]]
        assert "run_skill_script" not in names
