"""Tests for agent/plan_execute_agent.py."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from agent.plan_execute_agent import (
    _PlanToolEvents,
    _PlannerOutput,
    _TaskSpec,
    _extend_callbacks,
    _filter_final_synthesis_tasks,
    _is_final_synthesis_task,
    _planner_prompt,
    _summarizer_prompt,
)


# ── _PlannerOutput model validator ────────────────────────────────────────────

class TestPlannerOutput:
    def test_qwen_plan_key_alias(self):
        """Qwen often returns {"plan": [...]} instead of {"steps": [...]}."""
        out = _PlannerOutput.model_validate({"plan": ["a", "b"]})
        assert out.steps == ["a", "b"]

    def test_steps_takes_precedence_over_plan(self):
        """If both keys exist, steps wins."""
        out = _PlannerOutput.model_validate({"steps": ["x"], "plan": ["y"]})
        assert out.steps == ["x"]

    def test_empty_inputs_fall_back_to_empty_steps(self):
        assert _PlannerOutput(steps=[]).steps == []
        assert _PlannerOutput().steps == []
        assert _PlannerOutput.model_validate({"plan": []}).steps == []

    def test_steps_are_converted_to_chain_tasks(self):
        out = _PlannerOutput.model_validate({"steps": ["first", "second", "third"]})
        assert [task.title for task in out.tasks] == ["first", "second", "third"]
        assert out.tasks[0].depends_on == []
        assert out.tasks[1].depends_on == [out.tasks[0].id]
        assert out.tasks[2].depends_on == [out.tasks[0].id, out.tasks[1].id]

    def test_task_dag_is_accepted(self):
        out = _PlannerOutput.model_validate({
            "tasks": [
                {"id": "a", "title": "A", "description": "Do A", "depends_on": []},
                {"id": "b", "title": "B", "description": "Do B", "depends_on": ["a"]},
            ],
        })
        assert [task.id for task in out.tasks] == ["a", "b"]
        assert out.tasks[1].depends_on == ["a"]


def test_extend_callbacks_preserves_callback_manager_context():
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.callbacks.manager import AsyncCallbackManager

    class ExistingHandler(BaseCallbackHandler):
        pass

    class ExtraHandler(BaseCallbackHandler):
        pass

    existing = ExistingHandler()
    extra = ExtraHandler()
    manager = AsyncCallbackManager([], inheritable_handlers=[existing], tags=["outer"])

    extended = _extend_callbacks(manager, [extra])

    assert extended is not manager
    assert existing in extended.inheritable_handlers
    assert extra in extended.inheritable_handlers
    assert extra not in extended.handlers
    assert extended.tags == ["outer"]


def test_final_synthesis_task_detection_filters_obvious_final_answer_tasks():
    assert _is_final_synthesis_task(_TaskSpec(
        id="synthesize_answer",
        title="整合完整回答",
        description="根据前面结果撰写最终答案",
    ))
    assert _is_final_synthesis_task(_TaskSpec(
        id="write_final",
        title="Write final answer",
        description="Draft the final response for the user",
    ))


def test_final_synthesis_task_detection_keeps_intermediate_analysis_tasks():
    assert not _is_final_synthesis_task(_TaskSpec(
        id="analyze_findings",
        title="分析并整合研究发现",
        description="整合多来源证据并分析趋势",
    ))


def test_filter_final_synthesis_task_removes_terminal_task():
    tasks = [
        _TaskSpec(id="a", title="A", description="task A"),
        _TaskSpec(id="b", title="B", description="task B", depends_on=["a"]),
        _TaskSpec(
            id="synthesize_answer",
            title="整合完整回答",
            description="根据前面结果撰写最终答案",
            depends_on=["a", "b"],
        ),
    ]

    filtered = _filter_final_synthesis_tasks(tasks)

    assert [task.id for task in filtered] == ["a", "b"]
    assert filtered[1].depends_on == ["a"]


def test_filter_final_synthesis_task_rewires_downstream_dependencies():
    tasks = [
        _TaskSpec(id="a", title="A", description="task A"),
        _TaskSpec(
            id="final_report",
            title="撰写最终报告",
            description="汇总答案",
            depends_on=["a"],
        ),
        _TaskSpec(id="c", title="C", description="task C", depends_on=["final_report"]),
    ]

    filtered = _filter_final_synthesis_tasks(tasks)

    assert [task.id for task in filtered] == ["a", "c"]
    assert filtered[1].depends_on == ["a"]


def test_filter_final_synthesis_task_keeps_one_task_if_all_filtered():
    tasks = [
        _TaskSpec(id="final", title="最终回答", description="撰写最终答案"),
    ]

    filtered = _filter_final_synthesis_tasks(tasks)

    assert [task.id for task in filtered] == ["final"]
    assert filtered[0].depends_on == []


def test_plan_execute_prompts_include_guardrail_rules():
    from harness import HarnessConfig, guardrail_system_rules

    cfg = HarnessConfig()
    rules = guardrail_system_rules(cfg)
    planner_messages = _planner_prompt(cfg).format_messages(input="x")
    summarizer_messages = _summarizer_prompt(cfg).format_messages(input="x", past_steps="")

    assert rules
    assert rules in planner_messages[0].content
    assert rules in summarizer_messages[0].content


def test_plan_execute_planner_prompt_omits_skills_overview():
    from harness import HarnessConfig

    planner_messages = _planner_prompt(HarnessConfig()).format_messages(input="x")

    assert "## Available Skills" not in planner_messages[0].content


def test_plan_tool_events_match_inputs_by_tool_call_id():
    events = _PlanToolEvents()
    events.start("run_skill_script", "other input", tool_call_id="call-other")
    events.start("run_skill_script", "search input", tool_call_id="call-search")

    assert events.finish("run_skill_script", "call-search") == ("call-search", "search input")
    assert events.finish("run_skill_script", "call-other") == ("call-other", "other input")


# ── Step number calculation ───────────────────────────────────────────────────
#
# The formula in execute_step is:
#   total    = state["plan_total"]
#   step_num = total - len(state["plan"]) + 1
#
# We test the formula directly (pure arithmetic) across realistic scenarios.

    # ── single round ────────────────────────────────────────────────────────

    # ── multi-round: past_steps accumulates, but numbering must reset ────────

# ── plan_step + execute_step integration (mocked LLM) ────────────────────────

class TestPlanExecuteIntegration:
    """End-to-end tests with fully mocked LLM.

    After the async/streaming refactor:
      - planner is called via .ainvoke (async)
      - executor is called via .astream(stream_mode=["updates", "custom"])
      - summarizer is called via .astream

    One two-step run covers planner, executor context propagation and summarizer.
    """

    @pytest.fixture
    def shared_env(self):
        from unittest.mock import patch
        from agent.plan_execute_agent import _PlannerOutput
        from langchain_core.messages import AIMessageChunk

        holder = {"steps": [], "captured": []}

        # LCEL coerces non-Runnable callables (MagicMock) into RunnableLambda,
        # whose .ainvoke calls the wrapped function SYNCHRONOUSLY in an executor.
        # So we must set .side_effect/.return_value (the sync call result),
        # not .ainvoke — the latter would never be reached. If we got this wrong,
        # mock_chain() returns an auto-MagicMock; state.plan becomes a truthy
        # MagicMock that never empties; route_after_execute loops until
        # GraphRecursionError, each iteration ballooning the MagicMock tree.
        mock_chain = MagicMock()
        mock_chain.side_effect = lambda *_a, **_kw: _PlannerOutput(steps=holder["steps"])
        mock_chain.with_retry.return_value = mock_chain

        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_chain
        mock_llm.with_retry.return_value = mock_llm
        # summarizer chain: (_SUMMARIZER_PROMPT | mock_llm).astream yields one
        # chunk per inner RunnableLambda call; return an AIMessageChunk so
        # extract_text_content sees real text.
        mock_llm.return_value = AIMessageChunk(content="final answer")

        # The executor is the compiled ReAct sub-graph; in production it is a
        # real Pregel object so it's NOT coerced — execute_step calls
        # executor.astream(...) directly. An async generator function works here.
        async def fake_exec_astream(state, **_kwargs):
            holder["captured"].append(state["messages"][0][1])
            yield "custom", {"phase": "agent_text", "text": "step result"}
        mock_executor = MagicMock()
        mock_executor.astream = fake_exec_astream
        # PlanExecuteAgent builds its executor via ReActAgent(...).compiled — patch
        # the class so .compiled returns our fake executor graph.
        mock_react = MagicMock()
        mock_react.return_value.compiled = mock_executor

        with (
            patch("agent.llm.LLM.chat_model", return_value=mock_llm),
            patch("agent.plan_execute_agent.ReActAgent", mock_react),
        ):
            from agent.plan_execute_agent import create_plan_execute_agent
            agent = create_plan_execute_agent()
            yield agent, holder

    @staticmethod
    def _reset(holder, steps):
        holder["steps"] = list(steps)
        holder["captured"] = []

    @staticmethod
    def _initial_state(task="task"):
        return {"input": task, "plan": [], "plan_total": 0, "past_steps": [], "response": None}

    async def test_two_step_plan_executes_with_context_and_summarizes(self, shared_env):
        agent, holder = shared_env
        self._reset(holder, ["step 1", "step 2"])
        result = await agent.ainvoke(self._initial_state())

        assert len(holder["captured"]) == 2
        assert holder["captured"][0] == "step 1"
        assert "step 1" in holder["captured"][1] and "step result" in holder["captured"][1]
        assert result["response"] == "final answer"


async def test_independent_tasks_run_in_parallel_and_dependency_waits():
    from unittest.mock import MagicMock, patch
    import asyncio

    from harness import HarnessConfig
    from agent.plan_execute_agent import _PlannerOutput, create_plan_execute_agent
    from langchain_core.messages import AIMessageChunk

    holder = {"active": 0, "max_active": 0, "captured": []}
    planner_output = _PlannerOutput.model_validate({
        "tasks": [
            {"id": "a", "title": "A", "description": "task A", "depends_on": []},
            {"id": "b", "title": "B", "description": "task B", "depends_on": []},
            {"id": "c", "title": "C", "description": "task C", "depends_on": ["a", "b"]},
        ],
    })

    mock_chain = MagicMock()
    mock_chain.side_effect = lambda *_a, **_kw: planner_output
    mock_chain.with_retry.return_value = mock_chain

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain
    mock_llm.with_retry.return_value = mock_llm
    mock_llm.return_value = AIMessageChunk(content="final answer")

    async def fake_exec_astream(state, **_kwargs):
        query = state["messages"][0][1]
        holder["captured"].append(query)
        holder["active"] += 1
        holder["max_active"] = max(holder["max_active"], holder["active"])
        try:
            await asyncio.sleep(0.03)
            yield "custom", {
                "phase": "agent_text",
                "text": f"result for {query.splitlines()[0]}",
            }
        finally:
            holder["active"] -= 1

    mock_executor = MagicMock()
    mock_executor.astream = fake_exec_astream
    mock_react = MagicMock()
    mock_react.return_value.compiled = mock_executor

    with (
        patch("agent.llm.LLM.chat_model", return_value=mock_llm),
        patch("agent.plan_execute_agent.ReActAgent", mock_react),
    ):
        agent = create_plan_execute_agent(harness=HarnessConfig(max_parallel_tasks=2))
        result = await agent.ainvoke({
            "input": "test",
            "plan": [],
            "plan_total": 0,
            "tasks": [],
            "task_results": {},
            "task_errors": {},
            "past_steps": [],
            "response": None,
        })

    assert result["response"] == "final answer"
    assert holder["max_active"] == 2
    assert holder["captured"][0] == "task A"
    assert holder["captured"][1] == "task B"
    assert "Dependency results" in holder["captured"][2]
    assert "result for task A" in holder["captured"][2]
    assert "result for task B" in holder["captured"][2]


async def test_final_synthesis_task_is_not_executed_but_summarizer_runs():
    from unittest.mock import MagicMock, patch

    from agent.plan_execute_agent import _PlannerOutput, create_plan_execute_agent
    from langchain_core.messages import AIMessageChunk

    planner_output = _PlannerOutput.model_validate({
        "tasks": [
            {"id": "a", "title": "A", "description": "task A", "depends_on": []},
            {"id": "b", "title": "B", "description": "task B", "depends_on": ["a"]},
            {
                "id": "synthesize_answer",
                "title": "整合完整回答",
                "description": "根据前面结果撰写最终答案",
                "depends_on": ["a", "b"],
            },
        ],
    })

    mock_chain = MagicMock()
    mock_chain.side_effect = lambda *_a, **_kw: planner_output
    mock_chain.with_retry.return_value = mock_chain

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain
    mock_llm.with_retry.return_value = mock_llm
    mock_llm.return_value = AIMessageChunk(content="final answer")

    captured: list[str] = []

    async def fake_exec_astream(state, **_kwargs):
        query = state["messages"][0][1]
        captured.append(query)
        yield "custom", {
            "phase": "agent_text",
            "text": f"result for {query.splitlines()[0]}",
        }

    mock_executor = MagicMock()
    mock_executor.astream = fake_exec_astream
    mock_react = MagicMock()
    mock_react.return_value.compiled = mock_executor

    with (
        patch("agent.llm.LLM.chat_model", return_value=mock_llm),
        patch("agent.plan_execute_agent.ReActAgent", mock_react),
    ):
        agent = create_plan_execute_agent()
        result = await agent.ainvoke({
            "input": "test",
            "plan": [],
            "plan_total": 0,
            "tasks": [],
            "task_results": {},
            "task_errors": {},
            "past_steps": [],
            "response": None,
        })

    assert result["response"] == "final answer"
    assert len(captured) == 2
    assert captured[0] == "task A"
    assert "task B" in captured[1]
    assert all("最终答案" not in query and "整合完整回答" not in query for query in captured)


async def test_task_tool_budget_failure_is_local():
    from unittest.mock import MagicMock, patch

    from harness import HarnessConfig
    from agent.plan_execute_agent import _PlannerOutput, create_plan_execute_agent
    from langchain_core.messages import AIMessageChunk

    planner_output = _PlannerOutput.model_validate({
        "tasks": [
            {"id": "a", "title": "A", "description": "task A", "depends_on": []},
        ],
    })

    mock_chain = MagicMock()
    mock_chain.side_effect = lambda *_a, **_kw: planner_output
    mock_chain.with_retry.return_value = mock_chain

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain
    mock_llm.with_retry.return_value = mock_llm
    mock_llm.return_value = AIMessageChunk(content="final answer")

    async def fake_exec_astream(_state, **kwargs):
        callbacks = kwargs["config"]["callbacks"]
        handlers = list(getattr(callbacks, "handlers", callbacks))
        handlers += list(getattr(callbacks, "inheritable_handlers", []))
        for cb in handlers:
            if cb.__class__.__name__ == "TaskBudgetTracker":
                cb.on_tool_start({}, "x")
                cb.on_tool_start({}, "x")
        yield AIMessageChunk(content="unreachable"), {"langgraph_node": "agent"}

    mock_executor = MagicMock()
    mock_executor.astream = fake_exec_astream
    mock_react = MagicMock()
    mock_react.return_value.compiled = mock_executor

    with (
        patch("agent.llm.LLM.chat_model", return_value=mock_llm),
        patch("agent.plan_execute_agent.ReActAgent", mock_react),
    ):
        agent = create_plan_execute_agent(harness=HarnessConfig(max_tool_calls_per_task=1))
        result = await agent.ainvoke({
            "input": "test",
            "plan": [],
            "plan_total": 0,
            "tasks": [],
            "task_results": {},
            "task_errors": {},
            "past_steps": [],
            "response": None,
        })

    assert result["response"] == "final answer"
    assert "a" in result["task_errors"]
    assert "tool-call budget exceeded" in result["task_errors"]["a"]


async def test_second_task_thinking_is_forwarded_as_custom_event():
    from unittest.mock import MagicMock, patch

    from agent.plan_execute_agent import _PlannerOutput, create_plan_execute_agent
    from langchain_core.messages import AIMessageChunk

    planner_output = _PlannerOutput.model_validate({
        "tasks": [
            {"id": "a", "title": "A", "description": "task A", "depends_on": []},
            {"id": "b", "title": "B", "description": "task B", "depends_on": ["a"]},
        ],
    })

    mock_chain = MagicMock()
    mock_chain.side_effect = lambda *_a, **_kw: planner_output
    mock_chain.with_retry.return_value = mock_chain

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain
    mock_llm.with_retry.return_value = mock_llm
    mock_llm.return_value = AIMessageChunk(content="final answer")

    async def fake_exec_astream(state, **_kwargs):
        query = state["messages"][0][1]
        step_label = "second" if query.startswith("task B") else "first"
        yield "custom", {"phase": "agent_thinking", "text": f"{step_label} thinking"}
        yield "custom", {"phase": "agent_text", "text": f"{step_label} result"}

    mock_executor = MagicMock()
    mock_executor.astream = fake_exec_astream
    mock_react = MagicMock()
    mock_react.return_value.compiled = mock_executor

    with (
        patch("agent.llm.LLM.chat_model", return_value=mock_llm),
        patch("agent.plan_execute_agent.ReActAgent", mock_react),
    ):
        agent = create_plan_execute_agent()
        events = []
        async for mode, data in agent.astream({
            "input": "test",
            "plan": [],
            "plan_total": 0,
            "tasks": [],
            "task_results": {},
            "task_errors": {},
            "past_steps": [],
            "response": None,
        }, stream_mode=["custom", "updates"]):
            if mode == "custom" and data.get("phase") == "execute_thinking":
                events.append(data)

    assert any(e["step_num"] == 1 and e["text"] == "first thinking" for e in events)
    assert any(e["step_num"] == 2 and e["text"] == "second thinking" for e in events)


async def test_task_custom_agent_chunks_are_forwarded_without_duplicate_message():
    from unittest.mock import MagicMock, patch

    from agent.plan_execute_agent import _PlannerOutput, create_plan_execute_agent
    from langchain_core.messages import AIMessage, AIMessageChunk

    planner_output = _PlannerOutput.model_validate({
        "tasks": [
            {"id": "a", "title": "A", "description": "task A", "depends_on": []},
        ],
    })

    mock_chain = MagicMock()
    mock_chain.side_effect = lambda *_a, **_kw: planner_output
    mock_chain.with_retry.return_value = mock_chain

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain
    mock_llm.with_retry.return_value = mock_llm
    mock_llm.return_value = AIMessage(content="final answer")

    captured_stream_modes = []

    async def fake_exec_astream(_state, **kwargs):
        captured_stream_modes.append(kwargs.get("stream_mode"))
        yield "custom", {"phase": "agent_thinking", "text": "think "}
        yield "custom", {"phase": "agent_text", "text": "part "}
        yield "custom", {"phase": "agent_text", "text": "answer"}
        yield "messages", (
            AIMessageChunk(content="part answer"),
            {"langgraph_node": "agent"},
        )

    mock_executor = MagicMock()
    mock_executor.astream = fake_exec_astream
    mock_react = MagicMock()
    mock_react.return_value.compiled = mock_executor

    with (
        patch("agent.llm.LLM.chat_model", return_value=mock_llm),
        patch("agent.plan_execute_agent.ReActAgent", mock_react),
    ):
        agent = create_plan_execute_agent()
        thinking_events = []
        token_events = []
        async for mode, data in agent.astream({
            "input": "test",
            "plan": [],
            "plan_total": 0,
            "tasks": [],
            "task_results": {},
            "task_errors": {},
            "past_steps": [],
            "response": None,
        }, stream_mode=["custom", "updates"]):
            if mode == "custom" and data.get("phase") == "execute_thinking":
                thinking_events.append(data["text"])
            if mode == "custom" and data.get("phase") == "execute_token":
                token_events.append(data["text"])

    assert thinking_events == ["think "]
    assert token_events == ["part ", "answer"]
    assert captured_stream_modes == [["updates", "custom"]]


async def test_nested_react_streams_custom_tokens_for_second_task():
    from unittest.mock import patch

    from agent.plan_execute_agent import _PlannerOutput, create_plan_execute_agent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, AIMessageChunk
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
    from pydantic import PrivateAttr

    class FakePlanner:
        def with_retry(self, *_args, **_kwargs):
            return self

        def __call__(self, *_args, **_kwargs):
            return _PlannerOutput.model_validate({
                "tasks": [
                    {"id": "a", "title": "A", "description": "task A", "depends_on": []},
                    {"id": "b", "title": "B", "description": "task B", "depends_on": ["a"]},
                ],
            })

    class FakeChatModel(BaseChatModel):
        _calls: int = PrivateAttr(default=0)

        def bind_tools(self, _tools, **_kwargs):
            return self

        def with_retry(self, *_args, **_kwargs):
            return self

        def with_structured_output(self, *_args, **_kwargs):
            return FakePlanner()

        def _generate(self, _messages, stop=None, run_manager=None, **_kwargs):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="summary"))])

        async def _astream(self, _messages, stop=None, run_manager=None, **_kwargs):
            self._calls += 1
            n = self._calls
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    additional_kwargs={"reasoning_content": f"think{n} "},
                )
            )
            yield ChatGenerationChunk(message=AIMessageChunk(content=f"answer{n} "))

        @property
        def _llm_type(self):
            return "fake-chat"

    with patch("agent.llm.LLM.chat_model", return_value=FakeChatModel()):
        agent = create_plan_execute_agent()
        events = []
        async for mode, data in agent.astream({
            "input": "test",
            "plan": [],
            "plan_total": 0,
            "tasks": [],
            "task_results": {},
            "task_errors": {},
            "past_steps": [],
            "response": None,
        }, stream_mode=["custom", "updates"]):
            if mode == "custom" and data.get("phase") in {
                "execute_thinking",
                "execute_token",
                "execute_done",
            }:
                events.append(data)

    step2 = [event for event in events if event.get("step_num") == 2]
    step2_phases = [event["phase"] for event in step2]
    assert step2_phases[:3] == ["execute_thinking", "execute_token", "execute_done"]
    assert step2[0]["text"] == "think2 "
    assert step2[1]["text"] == "answer2 "


async def test_task_tool_result_is_forwarded_from_tools_update():
    from unittest.mock import MagicMock, patch

    from agent.plan_execute_agent import _PlannerOutput, create_plan_execute_agent
    from langchain_core.messages import AIMessage, ToolMessage

    planner_output = _PlannerOutput.model_validate({
        "tasks": [
            {"id": "a", "title": "A", "description": "task A", "depends_on": []},
        ],
    })

    mock_chain = MagicMock()
    mock_chain.side_effect = lambda *_a, **_kw: planner_output
    mock_chain.with_retry.return_value = mock_chain

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain
    mock_llm.with_retry.return_value = mock_llm
    mock_llm.return_value = AIMessage(content="final answer")

    async def fake_exec_astream(_state, **_kwargs):
        yield "updates", {
            "tools": {
                "messages": [
                    ToolMessage(content="tool result", name="search", tool_call_id="tool-call-1"),
                ],
            },
        }
        yield "updates", {"agent": {"messages": [AIMessage(content="done")]}}

    mock_executor = MagicMock()
    mock_executor.astream = fake_exec_astream
    mock_react = MagicMock()
    mock_react.return_value.compiled = mock_executor

    with (
        patch("agent.llm.LLM.chat_model", return_value=mock_llm),
        patch("agent.plan_execute_agent.ReActAgent", mock_react),
    ):
        agent = create_plan_execute_agent()
        events = []
        async for mode, data in agent.astream({
            "input": "test",
            "plan": [],
            "plan_total": 0,
            "tasks": [],
            "task_results": {},
            "task_errors": {},
            "past_steps": [],
            "response": None,
        }, stream_mode=["custom", "updates"]):
            if mode == "custom" and data.get("phase") == "execute_tool":
                events.append(data)

    assert len(events) == 1
    assert events[0]["name"] == "search"
    assert events[0]["tool_call_id"] == "tool-call-1"
    assert events[0]["result"] == "tool result"


async def test_task_tool_result_extracts_favicons_for_web_research_search_script():
    from unittest.mock import MagicMock, patch

    from agent.plan_execute_agent import _PlannerOutput, create_plan_execute_agent
    from langchain_core.messages import AIMessage, ToolMessage

    planner_output = _PlannerOutput.model_validate({
        "tasks": [
            {"id": "a", "title": "A", "description": "task A", "depends_on": []},
        ],
    })

    mock_chain = MagicMock()
    mock_chain.side_effect = lambda *_a, **_kw: planner_output
    mock_chain.with_retry.return_value = mock_chain

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain
    mock_llm.with_retry.return_value = mock_llm
    mock_llm.return_value = AIMessage(content="final answer")

    result = '{"results":[{"url":"https://example.com/a","favicon":"https://cdn.example.com/icon.png"}]}'

    async def fake_exec_astream(_state, **kwargs):
        callbacks = kwargs["config"]["callbacks"]
        handlers = getattr(callbacks, "handlers", None) or getattr(callbacks, "inheritable_handlers", None) or callbacks
        for cb in handlers:
            if cb.__class__.__name__ == "_PlanToolStartCallback":
                cb.on_tool_start(
                    {"name": "run_skill_script"},
                    '{"skill":"web-research","script":"search.py","script_args":["q"]}',
                    tool_call_id="tool-call-1",
                )
        yield "updates", {
            "tools": {
                "messages": [
                    ToolMessage(content=result, name="run_skill_script", tool_call_id="tool-call-1"),
                ],
            },
        }
        yield "updates", {"agent": {"messages": [AIMessage(content="done")]}}

    mock_executor = MagicMock()
    mock_executor.astream = fake_exec_astream
    mock_react = MagicMock()
    mock_react.return_value.compiled = mock_executor

    with (
        patch("agent.llm.LLM.chat_model", return_value=mock_llm),
        patch("agent.plan_execute_agent.ReActAgent", mock_react),
    ):
        agent = create_plan_execute_agent()
        events = []
        async for mode, data in agent.astream({
            "input": "test",
            "plan": [],
            "plan_total": 0,
            "tasks": [],
            "task_results": {},
            "task_errors": {},
            "past_steps": [],
            "response": None,
        }, stream_mode=["custom", "updates"]):
            if mode == "custom" and data.get("phase") == "execute_tool":
                events.append(data)

    assert events[0]["source_favicons"] == [{
        "url": "https://example.com/a",
        "favicon": "https://cdn.example.com/icon.png",
    }]


async def test_task_tool_result_is_not_duplicated_from_messages_and_updates():
    from unittest.mock import MagicMock, patch

    from agent.plan_execute_agent import _PlannerOutput, create_plan_execute_agent
    from langchain_core.messages import AIMessage, ToolMessage

    tool_msg = ToolMessage(content="tool result", name="search", tool_call_id="tool-call-1")
    planner_output = _PlannerOutput.model_validate({
        "tasks": [
            {"id": "a", "title": "A", "description": "task A", "depends_on": []},
        ],
    })

    mock_chain = MagicMock()
    mock_chain.side_effect = lambda *_a, **_kw: planner_output
    mock_chain.with_retry.return_value = mock_chain

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain
    mock_llm.with_retry.return_value = mock_llm
    mock_llm.return_value = AIMessage(content="final answer")

    async def fake_exec_astream(_state, **_kwargs):
        yield tool_msg, {"langgraph_node": "tools"}
        yield "updates", {"tools": {"messages": [tool_msg]}}
        yield "updates", {"agent": {"messages": [AIMessage(content="done")]}}

    mock_executor = MagicMock()
    mock_executor.astream = fake_exec_astream
    mock_react = MagicMock()
    mock_react.return_value.compiled = mock_executor

    with (
        patch("agent.llm.LLM.chat_model", return_value=mock_llm),
        patch("agent.plan_execute_agent.ReActAgent", mock_react),
    ):
        agent = create_plan_execute_agent()
        events = []
        async for mode, data in agent.astream({
            "input": "test",
            "plan": [],
            "plan_total": 0,
            "tasks": [],
            "task_results": {},
            "task_errors": {},
            "past_steps": [],
            "response": None,
        }, stream_mode=["custom", "updates"]):
            if mode == "custom" and data.get("phase") == "execute_tool":
                events.append(data)

    assert len(events) == 1


async def test_task_answer_falls_back_to_agent_update_when_chunks_are_missing():
    from unittest.mock import MagicMock, patch

    from agent.plan_execute_agent import _PlannerOutput, create_plan_execute_agent
    from langchain_core.messages import AIMessage

    planner_output = _PlannerOutput.model_validate({
        "tasks": [
            {"id": "a", "title": "A", "description": "task A", "depends_on": []},
            {"id": "b", "title": "B", "description": "task B", "depends_on": ["a"]},
        ],
    })

    mock_chain = MagicMock()
    mock_chain.side_effect = lambda *_a, **_kw: planner_output
    mock_chain.with_retry.return_value = mock_chain

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain
    mock_llm.with_retry.return_value = mock_llm
    mock_llm.return_value = AIMessage(content="final answer")

    async def fake_exec_astream(state, **_kwargs):
        query = state["messages"][0][1]
        answer = "second update answer" if query.startswith("task B") else "first update answer"
        yield "updates", {"agent": {"messages": [AIMessage(content=answer)]}}

    mock_executor = MagicMock()
    mock_executor.astream = fake_exec_astream
    mock_react = MagicMock()
    mock_react.return_value.compiled = mock_executor

    with (
        patch("agent.llm.LLM.chat_model", return_value=mock_llm),
        patch("agent.plan_execute_agent.ReActAgent", mock_react),
    ):
        agent = create_plan_execute_agent()
        token_events = []
        result = None
        async for mode, data in agent.astream({
            "input": "test",
            "plan": [],
            "plan_total": 0,
            "tasks": [],
            "task_results": {},
            "task_errors": {},
            "past_steps": [],
            "response": None,
        }, stream_mode=["custom", "updates"]):
            if mode == "custom" and data.get("phase") == "execute_token":
                token_events.append(data)
            if mode == "updates" and "summarize" in data:
                result = data["summarize"]

    assert any(e["step_num"] == 1 and e["text"] == "first update answer" for e in token_events)
    assert any(e["step_num"] == 2 and e["text"] == "second update answer" for e in token_events)
    assert result and result["response"] == "final answer"


# ── route_after_execute logic ─────────────────────────────────────────────────

# ── run_plan_execute_agent wrapper ────────────────────────────────────────────

class TestRunPlanExecuteAgent:
    def test_returns_response_string(self, mocker):
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"response": "final answer"})
        mocker.patch("agent.plan_execute_agent.create_plan_execute_agent", return_value=mock_agent)

        from agent.plan_execute_agent import run_plan_execute_agent
        result = run_plan_execute_agent("test query")
        assert result == "final answer"

    def test_returns_empty_string_when_no_response(self, mocker):
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"response": None})
        mocker.patch("agent.plan_execute_agent.create_plan_execute_agent", return_value=mock_agent)

        from agent.plan_execute_agent import run_plan_execute_agent
        assert run_plan_execute_agent("test") == ""
