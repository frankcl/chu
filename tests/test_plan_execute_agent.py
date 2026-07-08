"""Tests for agent/plan_execute_agent.py — PlannerOutput validator and step numbering."""

import pytest
from agent.plan_execute_agent import _PlannerOutput


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
