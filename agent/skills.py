"""Skill 支持：Claude Code 风格的 SKILL.md 文件夹 + 渐进式披露。

每个 skill 是一个文件夹，内含 `SKILL.md`（YAML frontmatter: name / description，
正文为指令），可附带脚本/资源文件。系统提示里只放每个 skill 的 name+description；
模型调用 `Skill` 工具按需展开完整指令，再用 `run_skill_script` 运行其附带脚本。
"""

import os
import re
import subprocess
import sys
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool, tool

from .log import get_logger

logger = get_logger("skills")

_SKILL_FILE = "SKILL.md"
# 普通 ReAct 每轮请求内，同一个 (skill, script) 最多可运行的次数（硬闸，超限
# 优雅拒绝而非中止整轮）。Plan-Execute 会按 plan_task_id 另行做每任务计数，
# 避免并发调研任务互相抢同一个搜索脚本 cap。
# 计数容器由服务端每轮放入 config["configurable"]["skill_call_counts"]；缺失则
# 不限制（fail-open：CLI / 未注入的路径不受影响）。
_MAX_SKILL_SCRIPT_CALLS_DEFAULT = 3
# 针对特定脚本的单独上限，覆盖全局 MAX_SKILL_SCRIPT_CALLS。键为 "skill/script"。
# 内置：ppt/build.py 每轮至多 2 次——与 skills/ppt/SKILL.md 的约定一致（一次构建、
# 至多一次修复重试），避免像纯全局上限那样在造出好几份垃圾 deck 之后才拦下。
# 可用环境变量 MAX_SKILL_SCRIPT_CALLS_OVERRIDES 覆盖或新增，形如
# "ppt/build.py=2,web-research/search.py=4"。
_SCRIPT_CALL_CAP_BUILTIN = {"ppt/build.py": 2}


def _script_call_cap(skill: str, script: str) -> int:
    """本轮内 (skill, script) 的调用上限：脚本级覆盖 > 全局 MAX_SKILL_SCRIPT_CALLS > 内置默认。"""
    overrides = dict(_SCRIPT_CALL_CAP_BUILTIN)
    raw = os.getenv("MAX_SKILL_SCRIPT_CALLS_OVERRIDES")
    if raw:
        for item in raw.split(","):
            item = item.strip()
            if not item or "=" not in item:
                continue
            k, v = item.split("=", 1)
            try:
                overrides[k.strip()] = int(v.strip())
            except ValueError:
                logger.warning("MAX_SKILL_SCRIPT_CALLS_OVERRIDES 项 %r 非法，已忽略", item)
    key = f"{skill}/{script}"
    if key in overrides:
        return overrides[key]
    return _env_int("MAX_SKILL_SCRIPT_CALLS", _MAX_SKILL_SCRIPT_CALLS_DEFAULT)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("env %s=%r is not an int, using %d", name, raw, default)
        return default
# 模型工具名约束（OpenAI / Anthropic 通用）：字母数字、下划线、连字符，长度 1–64。
_VALID_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Skill:
    name: str            # frontmatter name
    description: str     # frontmatter description（进系统提示）
    path: Path           # skill 文件夹绝对路径
    instructions: str    # SKILL.md 正文（Skill 工具按需返回）
    files: list[str] = field(default_factory=list)  # 文件夹内除 SKILL.md 外的相对文件


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """切分 `---\\n…\\n---\\n` frontmatter，返回 (meta, body)。

    无合法 frontmatter 时返回 ({}, 原文)。
    """
    if not text.startswith("---"):
        return {}, text
    # 第一行是 '---'，找下一处 '---' 作为 frontmatter 结束。
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta_block = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    try:
        meta = yaml.safe_load(meta_block) or {}
    except yaml.YAMLError as e:
        logger.warning("skill frontmatter parse failed: %s", e)
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, body


def _load_skill(skill_dir: Path) -> Skill | None:
    """从一个文件夹加载 Skill；缺 SKILL.md / 缺字段 → 记 warning 并返回 None。"""
    md = skill_dir / _SKILL_FILE
    if not md.is_file():
        return None
    meta, body = _parse_frontmatter(md.read_text(encoding="utf-8"))
    name = meta.get("name")
    description = meta.get("description")
    if not name or not description:
        logger.warning("skill at %s missing name/description in frontmatter — skipped", skill_dir)
        return None
    files = sorted(
        str(p.relative_to(skill_dir))
        for p in skill_dir.rglob("*")
        if p.is_file() and p.name != _SKILL_FILE
    )
    return Skill(
        name=str(name),
        description=str(description),
        path=skill_dir.resolve(),
        instructions=body,
        files=files,
    )


class SkillRegistry:
    """扫描 skills 目录，按 name 提供 skill 查询。"""

    def __init__(self, skills_dir: Path | None):
        self._root = skills_dir.resolve() if skills_dir else None
        self._skills: dict[str, Skill] = {}
        if self._root and self._root.is_dir():
            self._load()

    def _load(self) -> None:
        for child in sorted(self._root.iterdir()):
            if not child.is_dir():
                continue
            skill = _load_skill(child)
            if skill is None:
                continue
            if skill.name in self._skills:
                logger.warning("duplicate skill name %r at %s — keeping first", skill.name, child)
                continue
            self._skills[skill.name] = skill
        logger.info("loaded %d skill(s) from %s", len(self._skills), self._root)

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    @property
    def root(self) -> Path | None:
        return self._root

    def is_empty(self) -> bool:
        return not self._skills

    @classmethod
    def resolve(cls, skills) -> "SkillRegistry":
        """把多种入参规整为 SkillRegistry。

        - None  → 默认目录（env SKILLS_DIR > <repo_root>/skills）
        - False → 空注册表（显式禁用）
        - str / Path → 该目录
        - SkillRegistry → 原样返回
        """
        if isinstance(skills, SkillRegistry):
            return skills
        if skills is False:
            return cls(None)
        if skills is None:
            env_dir = os.getenv("SKILLS_DIR")
            skills_dir = Path(env_dir) if env_dir else _default_skills_dir()
            return cls(skills_dir)
        return cls(Path(skills))


def _default_skills_dir() -> Path:
    # repo_root = agent 包的上一级目录
    return Path(__file__).resolve().parent.parent / "skills"


def skills_overview(registry: SkillRegistry) -> str:
    """生成注入系统提示的 skill 概览块；空注册表返回 ''。"""
    skills = registry.list()
    if not skills:
        return ""
    lines = [
        "## Available Skills",
        "Each skill below is exposed as a tool whose name is the skill name. If a task "
        "matches a skill, you MUST invoke that skill tool FIRST (no arguments) and follow "
        "its instructions — do NOT start solving the task with other tools (e.g. search) "
        "before loading the skill. Use a skill only once per task; do not also do the same "
        "work manually. Do not guess a skill's contents. For skills with bundled scripts, "
        "use the run_skill_script tool as the instructions direct.",
        "",
    ]
    lines += [f"- {s.name}: {s.description}" for s in skills]
    return "\n".join(lines)


def _skill_expansion(s: Skill) -> str:
    """渐进式披露：被调用时返回该 skill 的完整指令 + 目录 + 文件清单。

    指令正文里的 `{{CURRENT_DATE}}` 占位符会在此处替换成实时日期——只有显式写了
    该占位符的（时间敏感）skill 才会拿到当前日期，避免污染基础系统提示。
    """
    files = "\n".join(f"  - {f}" for f in s.files) if s.files else "  (none)"
    instructions = s.instructions
    if "{{CURRENT_DATE}}" in instructions:
        today = datetime.now().astimezone().strftime("%Y-%m-%d (%A)")
        instructions = instructions.replace("{{CURRENT_DATE}}", today)
    return (
        f"# Skill: {s.name}\n\n"
        f"{instructions}\n\n"
        f"---\n"
        f"Skill directory: {s.path}\n"
        f"Bundled files:\n{files}\n"
        f"To run a bundled script, call run_skill_script(skill={s.name!r}, script=<relative path>, script_args=[...])."
    )


def _coerce_args(script_args) -> list[str]:
    """把 script_args 规整为 list[str]：None→[]，单值→[str]，list→逐项 str。"""
    if script_args is None:
        return []
    if isinstance(script_args, str):
        return [script_args]
    if isinstance(script_args, (list, tuple)):
        return [str(a) for a in script_args]
    return [str(script_args)]


def _make_skill_tool(s: Skill) -> BaseTool:
    """把一个 skill 暴露成同名工具：调用即加载其完整指令（无参数）。"""

    def _load() -> str:
        logger.info("tool=skill name=%s", s.name)
        return _skill_expansion(s)

    return StructuredTool.from_function(
        func=_load,
        name=s.name,
        # 描述进入工具 schema = 渐进式披露的“目录”；正文指令仅在被调用时返回。
        description=(
            f"{s.description}\n\n"
            "Call this tool (no arguments) to load the skill's full step-by-step "
            "instructions, then follow them."
        ),
    )


def build_skill_tools(registry: SkillRegistry, per_tool_timeout: float) -> list[BaseTool]:
    """构造 skill 相关工具；空注册表返回 []。

    每个 skill 暴露成一个同名工具（调用即加载完整指令），外加一个共享的
    run_skill_script 工具。这样模型可以直接按 skill 名调用，符合直觉。
    """
    if registry.is_empty():
        return []

    tools: list[BaseTool] = []
    for s in registry.list():
        if not _VALID_TOOL_NAME.match(s.name):
            logger.warning(
                "skill name %r is not a valid tool name (need ^[a-zA-Z0-9_-]{1,64}$) — "
                "not exposed as a tool", s.name,
            )
            continue
        tools.append(_make_skill_tool(s))

    @tool("run_skill_script")
    def run_skill_script(
        skill: str,
        script: str,
        script_args: list[str] | str | None = None,
        config: RunnableConfig = None,  # 运行时注入，不进入模型可见的工具 schema
    ) -> str:
        """Run a script bundled inside a skill's directory and return its output.

        Args:
            skill: the skill name (from Available Skills).
            script: path to the script RELATIVE to the skill directory (e.g. "count.py").
            script_args: arguments passed to the script. Accepts a list of strings, or a
                single string (treated as one argument). Omit if the script takes none.
        """
        arg_preview = "".join(_coerce_args(script_args))[:300]
        logger.info(
            "tool=run_skill_script skill=%s script=%s args=%s", skill, script, arg_preview,
        )
        s = registry.get(skill)
        if s is None:
            return f"Unknown skill {skill!r}."
        # 每轮硬闸：同一 (skill, script) 超过上限则优雅拒绝（返回字符串让模型据此作答，
        # 而不是抛异常中止整轮）。计数容器由服务端每轮注入；缺失则不限制。
        counts = None
        try:
            configurable = (config or {}).get("configurable", {})
            counts = configurable.get("skill_call_counts")
        except Exception:  # noqa: BLE001 — 计数不可用时一律放行
            counts = None
        if counts is not None:
            base_key = f"{skill}/{script}"
            task_id = configurable.get("plan_task_id")
            if task_id:
                cap = int(configurable.get("max_skill_script_calls_per_task") or 3)
                key = f"task:{task_id}:{base_key}"
            else:
                cap = _script_call_cap(skill, script)
                key = f"global:{base_key}"
            counts[key] = counts.get(key, 0) + 1
            if counts[key] > cap:
                logger.info("skill script cap hit key=%s count=%d cap=%d", key, counts[key], cap)
                return (
                    f"[call cap reached: '{key}' already ran {cap} time(s) this turn. "
                    "Stop calling it and answer with the results you already have.]"
                )
        # 路径白名单：解析后必须仍位于该 skill 目录内（防 ../ 与绝对路径穿越）。
        target = (s.path / script).resolve()
        if not target.is_relative_to(s.path):
            return f"Refused: script path {script!r} escapes the skill directory."
        if not target.is_file():
            return f"Script not found: {script!r} in skill {skill!r}."
        # 模型常把单个参数误传成字符串而非列表 — 一律规整为 list[str]。
        cmd = [sys.executable, str(target), *_coerce_args(script_args)]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=per_tool_timeout,
                cwd=str(s.path),
            )
        except subprocess.TimeoutExpired:
            return f"[script timeout: {script} exceeded {per_tool_timeout:g}s]"
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        parts = [f"exit_code={proc.returncode}"]
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        return "\n".join(parts)

    tools.append(run_skill_script)
    return tools
