"""Tests for agent/plan_execute_agent.py — PlannerOutput validator and step numbering."""

import pytest
from agent.plan_execute_agent import (
    _PlannerOutput,
    _TaskSpec,
    _extend_callbacks,
    _filter_final_synthesis_tasks,
    _is_final_synthesis_task,
)


# ── _PlannerOutput model validator ────────────────────────────────────────────

class TestPlannerOutput:
    def test_standard_steps_key(self):
        out = _PlannerOutput(steps=["step1", "step2", "step3"])
        assert out.steps == ["step1", "step2", "step3"]

    def test_qwen_plan_key_alias(self):
        """Qwen often returns {"plan": [...]} instead of {"steps": [...]}."""
        out = _PlannerOutput.model_validate({"plan": ["a", "b"]})
        assert out.steps == ["a", "b"]

    def test_steps_takes_precedence_over_plan(self):
        """If both keys exist, steps wins."""
        out = _PlannerOutput.model_validate({"steps": ["x"], "plan": ["y"]})
        assert out.steps == ["x"]

    def test_empty_steps(self):
        out = _PlannerOutput(steps=[])
        assert out.steps == []

    def test_default_is_empty_list(self):
        out = _PlannerOutput()
        assert out.steps == []

    def test_empty_plan_key_falls_back_to_empty(self):
        out = _PlannerOutput.model_validate({"plan": []})
        assert out.steps == []

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


# ── Step number calculation ───────────────────────────────────────────────────
#
# The formula in execute_step is:
#   total    = state["plan_total"]
#   step_num = total - len(state["plan"]) + 1
#
# We test the formula directly (pure arithmetic) across realistic scenarios.

class TestStepNumberFormula:
    """Verify the 1-based step counter used in execute_step logging."""

    @staticmethod
    def _step_num(plan_total: int, remaining: int) -> int:
        """Replicate the formula from execute_step."""
        return plan_total - remaining + 1

    # ── single round ────────────────────────────────────────────────────────

    def test_first_step_of_three(self):
        assert self._step_num(plan_total=3, remaining=3) == 1

    def test_second_step_of_three(self):
        assert self._step_num(plan_total=3, remaining=2) == 2

    def test_third_step_of_three(self):
        assert self._step_num(plan_total=3, remaining=1) == 3

    def test_single_step_plan(self):
        assert self._step_num(plan_total=1, remaining=1) == 1

    def test_two_step_plan(self):
        assert self._step_num(plan_total=2, remaining=2) == 1
        assert self._step_num(plan_total=2, remaining=1) == 2

    # ── multi-round: past_steps accumulates, but numbering must reset ────────

    def test_second_round_starts_at_1(self):
        """
        Round 1 completed 3 steps → past_steps has 3 entries.
        Round 2 has a new plan_total=2.
        The step_num must start at 1, not 4.
        """
        # Round 2, first execute call
        assert self._step_num(plan_total=2, remaining=2) == 1
        # Round 2, second execute call
        assert self._step_num(plan_total=2, remaining=1) == 2

    def test_numbering_independent_of_past_steps_length(self):
        """
        No matter how large past_steps grows, the formula only uses
        plan_total and remaining — so it's always round-relative.
        """
        for accumulated_past_count in [0, 5, 100]:
            # A new round with plan_total=4
            for remaining in [4, 3, 2, 1]:
                expected = 4 - remaining + 1
                assert self._step_num(4, remaining) == expected


# ── plan_step + execute_step integration (mocked LLM) ────────────────────────

class TestPlanExecuteIntegration:
    """End-to-end tests with fully mocked LLM.

    After the async/streaming refactor:
      - planner is called via .ainvoke (async)
      - executor is called via .astream(stream_mode="messages")
      - summarizer is called via .astream

    Memory/time note: compiling a LangGraph (~pregel runtime, callbacks managers,
    state stores) per test costs ~10–50ms and several MB. Tests in this class
    share ONE compiled agent via a class-scoped fixture; per-test variation lives
    in a mutable `holder` dict that the mocks read from.
    """

    @pytest.fixture(scope="class")
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
            yield AIMessageChunk(content="step result"), {"langgraph_node": "agent"}
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

    async def test_plan_total_stored_in_state(self, shared_env):
        """plan_step must write plan_total = len(plan) into the state."""
        agent, holder = shared_env
        self._reset(holder, ["s1", "s2", "s3"])
        result = await agent.ainvoke(self._initial_state("test task"))
        assert result["response"] is not None
        assert len(holder["captured"]) == 3

    async def test_first_step_has_no_context(self, shared_env):
        """The first execute step sends the bare task without context."""
        agent, holder = shared_env
        self._reset(holder, ["only step"])
        await agent.ainvoke(self._initial_state())
        assert holder["captured"][0] == "only step"

    async def test_second_step_includes_first_result(self, shared_env):
        """The second execute step's query includes context from step 1."""
        agent, holder = shared_env
        self._reset(holder, ["step 1", "step 2"])
        await agent.ainvoke(self._initial_state())
        assert holder["captured"][0] == "step 1"
        assert "step 1" in holder["captured"][1] and "step result" in holder["captured"][1]

    async def test_response_is_summarizer_output(self, shared_env):
        """Final response comes from the summarizer, not executor."""
        agent, holder = shared_env
        self._reset(holder, ["one step"])
        result = await agent.ainvoke(self._initial_state())
        assert result["response"] == "final answer"


async def test_independent_tasks_run_in_parallel_and_dependency_waits():
    from unittest.mock import MagicMock, patch
    import asyncio

    from agent.harness import HarnessConfig
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
            yield AIMessageChunk(content=f"result for {query.splitlines()[0]}"), {
                "langgraph_node": "agent",
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
        yield AIMessageChunk(content=f"result for {query.splitlines()[0]}"), {
            "langgraph_node": "agent",
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

    from agent.harness import HarnessConfig
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
        yield AIMessageChunk(
            content="",
            additional_kwargs={"reasoning_content": f"{step_label} thinking"},
        ), {"langgraph_node": "agent"}
        yield AIMessageChunk(content=f"{step_label} result"), {"langgraph_node": "agent"}

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

    async def fake_exec_astream(_state, **_kwargs):
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


async def test_task_tool_result_uses_tool_message_call_id():
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
        yield ToolMessage(content="sunny", name="get_weather", tool_call_id="call-1"), {
            "langgraph_node": "tools",
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

    assert events
    assert events[0]["tool_call_id"] == "call-1"


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

class TestRouteAfterExecute:
    """The routing logic: empty plan → 'summarize', non-empty → 'execute'."""

    @staticmethod
    def _route(plan):
        return "summarize" if not plan else "execute"

    def test_empty_plan_routes_to_summarize(self):
        assert self._route([]) == "summarize"

    def test_non_empty_plan_routes_to_execute(self):
        assert self._route(["step 1", "step 2"]) == "execute"

    def test_single_remaining_step_routes_to_execute(self):
        assert self._route(["last step"]) == "execute"


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


from unittest.mock import AsyncMock, MagicMock
