"""第三层：Plan-and-Execute Agent 封装。

`PlanExecuteAgent` 复用第二层 `ReActAgent` 作为每一步的执行器，
整体分三个阶段：规划（planner）→ 逐步执行（execute 循环）→ 汇总（summarizer）。
"""

import asyncio
import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessageChunk, ToolMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, model_validator

from .harness import HarnessConfig, apply_llm_retry
from .llm import LLM
from .log import get_logger
from .react_agent import ReActAgent
from .skills import SkillRegistry, skills_overview

logger = get_logger("plan_execute_agent")


class _PlannerOutput(BaseModel):
    steps: list[str] = Field(default_factory=list, description="Ordered steps to complete the task")

    @model_validator(mode="before")
    @classmethod
    def _accept_plan_key(cls, data):
        # Qwen often returns {"plan": [...]} instead of {"steps": [...]}
        if isinstance(data, dict) and not data.get("steps") and data.get("plan"):
            data = {**data, "steps": data["plan"]}
        return data


class PlanExecuteState(TypedDict):
    input: str
    plan: list[str]
    plan_total: int  # total steps in the current round's plan (set by plan_step, not accumulated)
    past_steps: Annotated[list[tuple[str, str]], operator.add]
    response: str | None


_PLANNER_SYSTEM = (
    "Break the task into 3–5 broad, concrete steps. "
    "Prefer fewer steps that group related sub-actions over many narrow steps. "
    "Each step must be independently executable and meaningfully advance the task. "
    "Write each step in the same language as the user's task. "
    'Respond in JSON: {{"steps": [<string>, ...]}}.'
)


def _planner_prompt(registry: SkillRegistry) -> ChatPromptTemplate:
    """Planner prompt；当存在 skill 时把概览追加进 system 消息，使规划感知 skill。"""
    system = _PLANNER_SYSTEM
    if not registry.is_empty():
        overview = skills_overview(registry).replace("{", "{{").replace("}", "}}")
        system = system + "\n\n" + overview
    return ChatPromptTemplate.from_messages([("system", system), ("human", "{input}")])


_SUMMARIZER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "Based on the completed steps below, write a clear and concise final answer "
        "to the original task. Write the final answer in the same language as the "
        "user's original task.",
    ),
    (
        "human",
        "Task: {input}\n\nCompleted steps:\n{past_steps}",
    ),
])


class PlanExecuteAgent:
    """Plan-and-execute agent：规划 → 逐步执行（复用 ReActAgent）→ 汇总。"""

    def __init__(
        self,
        llm: LLM | None = None,
        extra_tools: list | None = None,
        checkpointer=None,
        harness: HarnessConfig | None = None,
        skills=None,
        hitl_channel=None,
    ):
        self.llm = llm or LLM()
        cfg = harness or HarnessConfig.from_env()
        # thinking=False is required for planner and summarizer:
        # structured-output (JSON mode) and thinking mode are mutually exclusive on
        # Qwen3 — with thinking on the model returns empty content, causing a parse error.
        base_no_think = self.llm.chat_model(thinking=False)
        # Skills：下传给执行器（每步用的 ReActAgent），并让 planner 感知可用 skill。
        registry = SkillRegistry.resolve(skills)
        # 第三层复用第二层：executor 是一个 ReActAgent 的 compiled graph。
        # 下传 hitl_channel：HITL 工具活在内层执行器里、由它阻塞；hitl 事件直接进会话
        # 队列（不经 get_stream_writer），因此能照常到达客户端。
        self._executor = ReActAgent(
            llm=self.llm, extra_tools=extra_tools, harness=cfg, skills=registry,
            hitl_channel=hitl_channel,
        ).compiled
        # with_structured_output / retry order matters: bind/configure on the raw
        # BaseChatModel first, then wrap the resulting Runnable with retry.
        self._planner = _planner_prompt(registry) | apply_llm_retry(
            base_no_think.with_structured_output(_PlannerOutput), cfg,
        )
        self._summarizer = _SUMMARIZER_PROMPT | apply_llm_retry(base_no_think, cfg)

        self.compiled = self._build_graph(checkpointer)

    def _build_graph(self, checkpointer):
        executor = self._executor
        planner = self._planner
        summarizer = self._summarizer

        async def plan_step(state: PlanExecuteState):
            writer = get_stream_writer()
            writer({"phase": "planning_start"})
            result = await planner.ainvoke({"input": state["input"]})
            logger.info("plan created: %d steps", len(result.steps))
            logger.debug("plan steps: %s", result.steps)
            return {"plan": result.steps, "plan_total": len(result.steps)}

        async def execute_step(state: PlanExecuteState, config):
            writer = get_stream_writer()
            task = state["plan"][0]
            past = state.get("past_steps") or []
            # Derive 1-based step number from plan position — never use len(past),
            # because past_steps accumulates across rounds via operator.add.
            total = state.get("plan_total") or len(state["plan"])
            step_num = total - len(state["plan"]) + 1
            logger.info("execute step [%d/%d]: %.80s", step_num, total, task)
            writer({"phase": "execute_start", "step_num": step_num,
                    "total": total, "task": task})

            context = "\n\n".join(f"Step: {s}\nResult: {r}" for s, r in past)
            query = f"{task}\n\nContext from prior steps:\n{context}" if context else task

            # Stream inner ReAct executor's tokens/tool results out via writer so the
            # client sees continuous progress instead of a 5–30s black box per step.
            # Thinking tokens (Anthropic extended thinking / Qwen reasoning_content)
            # are emitted as their own phase so the UI can show them live too —
            # otherwise step_start → first text can sit silent for tens of seconds.
            # Propagate the parent run's config into the inner executor so the
            # harness guards set up by the caller (server.py / main.py) actually
            # apply inside each step: the BudgetTracker callback (token / tool-call
            # budget), the shared skill_call_counts cap (so e.g. build.py can't be
            # called unbounded across steps), and recursion_limit. Without this the
            # inner astream starts a fresh root run and silently drops all of them.
            parts: list[str] = []
            async for chunk, meta in executor.astream(
                {"messages": [("human", query)]},
                config=config,
                stream_mode="messages",
            ):
                node = meta.get("langgraph_node")
                if node == "agent" and isinstance(chunk, AIMessageChunk):
                    for kind, text in LLM.iter_outputs(chunk):
                        if kind == "thinking":
                            writer({"phase": "execute_thinking", "step_num": step_num, "text": text})
                        else:
                            parts.append(text)
                            writer({"phase": "execute_token", "step_num": step_num, "text": text})
                elif node == "tools" and isinstance(chunk, ToolMessage):
                    result = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                    writer({"phase": "execute_tool", "step_num": step_num,
                            "name": chunk.name, "result": result[:800]})

            answer = "".join(parts)
            logger.debug("step result: %.200s", answer)
            # Advance the plan deterministically — no LLM decides when to stop.
            return {"past_steps": [(task, answer)], "plan": state["plan"][1:]}

        async def summarize_step(state: PlanExecuteState):
            writer = get_stream_writer()
            writer({"phase": "summarize_start"})
            logger.info("summarize: %d steps completed", len(state["past_steps"]))
            past_str = "\n".join(f"Step: {s}\nResult: {r}" for s, r in state["past_steps"])

            parts: list[str] = []
            async for chunk in summarizer.astream({"input": state["input"], "past_steps": past_str}):
                if not hasattr(chunk, "content"):
                    continue
                for kind, text in LLM.iter_outputs(chunk):
                    if kind == "thinking":
                        writer({"phase": "summarize_thinking", "text": text})
                    else:
                        parts.append(text)
                        writer({"phase": "summarize_token", "text": text})
            return {"response": "".join(parts)}

        def route_after_execute(state: PlanExecuteState) -> str:
            return "summarize" if not state["plan"] else "execute"

        graph = StateGraph(PlanExecuteState)
        graph.add_node("plan", plan_step)
        graph.add_node("execute", execute_step)
        graph.add_node("summarize", summarize_step)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "execute")
        graph.add_conditional_edges("execute", route_after_execute)
        graph.add_edge("summarize", END)

        return graph.compile(checkpointer=checkpointer)

    # 代理底层 compiled graph，保持调用方流式接口不变。
    def stream(self, *args, **kwargs):
        return self.compiled.stream(*args, **kwargs)

    def astream(self, *args, **kwargs):
        return self.compiled.astream(*args, **kwargs)

    def invoke(self, *args, **kwargs):
        return self.compiled.invoke(*args, **kwargs)

    async def arun(self, query: str) -> str:
        result = await self.compiled.ainvoke({
            "input": query,
            "plan": [],
            "plan_total": 0,
            "past_steps": [],
            "response": None,
        })
        return result.get("response") or ""

    def run(self, query: str) -> str:
        return asyncio.run(self.arun(query))


# ── 向后兼容的模块级函数 ────────────────────────────────────────────────────

def create_plan_execute_agent(**kwargs):
    """兼容旧 API：返回编译后的 plan-execute graph。"""
    return PlanExecuteAgent(**kwargs).compiled


def run_plan_execute_agent(query: str, **kwargs) -> str:
    """兼容旧 API：单次运行并返回最终文本。"""
    agent = create_plan_execute_agent(**kwargs)

    async def _run():
        return await agent.ainvoke({
            "input": query,
            "plan": [],
            "plan_total": 0,
            "past_steps": [],
            "response": None,
        })

    result = asyncio.run(_run())
    return result.get("response") or ""
