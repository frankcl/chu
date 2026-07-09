"""第二层：ReAct Agent 封装。

`ReActAgent` 在第一层 `LLM` 之上构建一个 ReAct 循环（LangGraph 状态机）：
agent 节点调用 LLM，tools 节点执行工具，循环至模型不再请求工具为止。
"""

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage, BaseMessageChunk, SystemMessage, message_chunk_to_message
from langchain_core.runnables import RunnableLambda
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from .harness import HarnessConfig, apply_llm_retry, wrap_tools
from .hitl import HitlChannel, make_request_user_choice_tool
from .llm import LLM
from .log import get_logger
from .skills import SkillRegistry, build_skill_tools, skills_overview
from .tools import get_builtin_tools

logger = get_logger("react_agent")


DEFAULT_SYSTEM = (
    "You are a helpful assistant. Use the available tools when needed "
    "to answer questions accurately. "
    "Always think and reply in the same language as the user's latest message: "
    "your reasoning (thinking / reasoning) and your final answer must both use that "
    "language (e.g. a Chinese question → answer in Chinese; an English question → "
    "answer in English), unless the user explicitly requests another language."
)


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


class ReActAgent:
    """ReAct agent：LLM ↔ 工具循环，编译为 LangGraph 状态机。"""

    def __init__(
        self,
        llm: LLM | None = None,
        extra_tools: list | None = None,
        system_prompt: str = DEFAULT_SYSTEM,
        checkpointer=None,
        harness: HarnessConfig | None = None,
        skills=None,
        hitl_channel: HitlChannel | None = None,
    ):
        self.llm = llm or LLM()
        cfg = harness or HarnessConfig.from_env()
        # Skills：发现可用 skill，把概览注入系统提示，并加入 Skill / run_skill_script 工具。
        # 无 skills 目录时注册表为空 → 工具与系统提示均不变（行为零回归）。
        registry = SkillRegistry.resolve(skills)
        skill_tools = build_skill_tools(registry, cfg.per_tool_timeout)
        if not registry.is_empty():
            system_prompt = system_prompt + "\n\n" + skills_overview(registry)
        tools = wrap_tools(get_builtin_tools() + skill_tools + (extra_tools or []), cfg)
        # HITL 工具必须**不**经 _TimeoutTool 包裹（它在独立线程跑工具，破坏 await 且
        # 无法支持不定时长的人工等待），所以在 wrap_tools 之后单独追加；它仍会进入
        # bind_tools 与 ToolNode。
        if hitl_channel is not None:
            tools = tools + [make_request_user_choice_tool(hitl_channel)]
        # bind_tools / with_structured_output are BaseChatModel methods — must run
        # on the raw LLM before wrapping with_retry, which returns a RunnableRetry.
        bound = apply_llm_retry(self.llm.chat_model().bind_tools(tools), cfg)
        tool_node = ToolNode(tools)

        def call_model(state: AgentState):
            logger.debug("agent node invoked, messages=%d", len(state["messages"]))
            messages = [SystemMessage(content=system_prompt)] + state["messages"]
            response = bound.invoke(messages)
            if response.tool_calls:
                logger.debug("agent requested tools: %s", [tc["name"] for tc in response.tool_calls])
            return {"messages": [response]}

        async def acall_model(state: AgentState):
            logger.debug("agent node invoked, messages=%d", len(state["messages"]))
            messages = [SystemMessage(content=system_prompt)] + state["messages"]
            try:
                writer = get_stream_writer()
            except RuntimeError:
                writer = None
            chunk: BaseMessageChunk | None = None
            async for part in bound.astream(messages):
                if isinstance(part, BaseMessageChunk):
                    if writer is not None:
                        for kind, text in LLM.iter_outputs(part):
                            writer({"phase": f"agent_{kind}", "text": text})
                    chunk = part if chunk is None else chunk + part
                else:
                    response = part
                    break
            else:
                if chunk is None:
                    response = await bound.ainvoke(messages)
                else:
                    response = message_chunk_to_message(chunk)
            if response.tool_calls:
                logger.debug("agent requested tools: %s", [tc["name"] for tc in response.tool_calls])
            return {"messages": [response]}

        def should_continue(state: AgentState) -> str:
            last = state["messages"][-1]
            return "tools" if last.tool_calls else END

        graph = StateGraph(AgentState)
        graph.add_node("agent", RunnableLambda(call_model, afunc=acall_model))
        graph.add_node("tools", tool_node)
        graph.add_edge(START, "agent")
        graph.add_conditional_edges("agent", should_continue)
        graph.add_edge("tools", "agent")

        self.compiled = graph.compile(checkpointer=checkpointer)

    # 代理底层 compiled graph，保持 main.py / server.py 的流式调用接口不变。
    def stream(self, *args, **kwargs):
        return self.compiled.stream(*args, **kwargs)

    def astream(self, *args, **kwargs):
        return self.compiled.astream(*args, **kwargs)

    def invoke(self, *args, **kwargs):
        return self.compiled.invoke(*args, **kwargs)

    def run(self, query: str) -> str:
        """Single-shot: run the agent on a query and return the final text response."""
        result = self.compiled.invoke({"messages": [("human", query)]})
        return LLM.extract_text(result["messages"][-1].content)


# ── 向后兼容的模块级 helper / 函数 ──────────────────────────────────────────

# 解析 helper 现在属于第一层 LLM，这里 re-export 以兼容旧导入。
extract_text_content = LLM.extract_text
iter_chunk_outputs = LLM.iter_outputs


def create_agent(**kwargs):
    """兼容旧 API：返回编译后的 ReAct graph。"""
    return ReActAgent(**kwargs).compiled


def run_agent(query: str, **kwargs) -> str:
    """兼容旧 API：单次运行并返回最终文本。"""
    agent = create_agent(**kwargs)
    result = agent.invoke({"messages": [("human", query)]})
    return LLM.extract_text(result["messages"][-1].content)
