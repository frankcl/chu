from .log import setup_logging

setup_logging()  # initialize logging as soon as the agent package is imported

# Imports follow setup_logging() so submodule loggers inherit the configured
# handlers — the E402 ordering is deliberate.
from .llm import LLM, extract_text_content, get_llm, iter_chunk_outputs  # noqa: E402
from .react_agent import DEFAULT_SYSTEM, ReActAgent, create_agent, run_agent  # noqa: E402
from .plan_execute_agent import (  # noqa: E402
    PlanExecuteAgent,
    create_plan_execute_agent,
    run_plan_execute_agent,
)
from .skills import Skill, SkillRegistry  # noqa: E402
from .harness import BudgetExceededError, BudgetTracker, HarnessConfig  # noqa: E402
from .hitl import ConsoleHitlChannel, HitlChannel  # noqa: E402

__all__ = [
    # 三层类
    "LLM",
    "ReActAgent",
    "PlanExecuteAgent",
    # 向后兼容的函数 / helper
    "create_agent",
    "run_agent",
    "create_plan_execute_agent",
    "run_plan_execute_agent",
    "get_llm",
    "extract_text_content",
    "iter_chunk_outputs",
    "DEFAULT_SYSTEM",
    # skills
    "Skill",
    "SkillRegistry",
    # harness
    "HarnessConfig",
    "BudgetTracker",
    "BudgetExceededError",
    # hitl
    "HitlChannel",
    "ConsoleHitlChannel",
]
