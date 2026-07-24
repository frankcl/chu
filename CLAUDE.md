# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A multi-provider LLM agent (Anthropic / OpenAI / Qwen) built on LangGraph, exposed both as a CLI and a FastAPI streaming server with a Vue 3 chat frontend. Comments and many prompts are written in Chinese; the default agent persona is instructed to think and reply in the user's own language (matching the language of the user's question).

## Commands

Python is managed with `uv` (see `uv.lock`, `.python-version`).

```bash
uv sync                              # install deps (incl. dev group)
uv run python main.py                # CLI, ReAct mode (default)
uv run python main.py --mode plan-execute   # CLI, plan-and-execute mode
uv run uvicorn server:app --reload   # API server on :8000
uv run pytest                        # full test suite (asyncio_mode=auto)
uv run pytest tests/test_harness.py::test_name   # single test
uv run ruff check .                  # lint
uv run ruff check --fix .            # lint + autofix

# Frontend (frontend/) — Vite dev server expects the API at :8000; CORS allows :5173
cd frontend && npm install && npm run dev
npm run build                        # outputs to frontend/dist/
```

Configuration is via `.env` (copy from `.env.example`). Key vars: `LLM_PROVIDER` (`anthropic`|`openai`|`qwen`), `MODEL_NAME`, provider API keys, `ENABLE_THINKING`, plus the harness knobs (see below).

## Architecture

### Three-layer agent stack (`agent/`)

The agent is built as three composable layers, each wrapping the one below. Module docstrings label them 第一/二/三层 (layer 1/2/3):

1. **`llm.py` — `LLM`**: unifies the three providers behind one class. Handles provider selection, thinking/reasoning mode resolution (explicit arg > env var > model-name heuristic), and output parsing. Two provider quirks live here and matter project-wide:
   - **Qwen** is reached through the OpenAI-compatible DashScope endpoint. langchain-openai drops the `reasoning_content` delta, so `_QwenChatOpenAI` is a dynamically-built subclass that rescues it into `additional_kwargs`.
   - **Structured output (JSON) and thinking mode are mutually exclusive on Qwen3** — with thinking on, the model returns empty content and JSON parsing fails. Anything using `with_structured_output` must call `chat_model(thinking=False)`.
   - `LLM.iter_outputs(chunk)` yields `(kind, text)` where kind ∈ {`thinking`, `text`}, normalizing Qwen `reasoning_content`, Anthropic thinking blocks, and plain text into one stream. Both `main.py` and `server.py` rely on it.

2. **`react_agent.py` — `ReActAgent`**: a LangGraph state machine with `agent` ↔ `tools` nodes looping until the model stops requesting tools. Tools = builtins + skill tools + any `extra_tools`, all run through `wrap_tools`. The system prompt is `DEFAULT_SYSTEM` plus an injected skills overview.

3. **`plan_execute_agent.py` — `PlanExecuteAgent`**: planner → execute-loop → summarizer. The executor for each step is a `ReActAgent` (layer 3 reuses layer 2). The plan advances **deterministically** (`plan[1:]` each step, step number derived from plan position — never `len(past_steps)`, which accumulates across rounds via `operator.add`). Inner executor tokens/thinking/tool results are forwarded out via LangGraph's `get_stream_writer()` (`custom` stream mode) so the client sees live progress instead of a per-step black box.

`agent/__init__.py` re-exports both the classes and backward-compatible `create_*` / `run_*` functions. The `create_agent` / `create_plan_execute_agent` functions return the **compiled graph**, not the wrapper class.

### Harness (`agent/harness.py`)

Centralized runtime safety, configured per-session via `HarnessConfig` (env defaults, overridable through the API). Components:
- **`BudgetTracker`**: a LangChain callback enforcing per-request token + tool-call budgets; raises `BudgetExceededError` which bubbles through LangGraph and is caught by the server to emit a `limit` SSE event.
- **`wrap_tools`**: applies allow/deny lists and wraps each tool in `_TimeoutTool` (per-call daemon thread, returns a timeout *string* rather than raising so the agent can react).
- **`apply_llm_retry`**: exponential-backoff retry on transient provider errors. Order matters — call `bind_tools`/`with_structured_output` on the raw `BaseChatModel` **before** wrapping with retry, since retry returns a `Runnable`, not a `BaseChatModel`.
- `recursion_limit` is *not* enforced here; it is passed to LangGraph at `astream()` time via `config={"recursion_limit": ...}`.

Env knobs: `RECURSION_LIMIT`, `IDLE_TIMEOUT_SECONDS`, `PER_TOOL_TIMEOUT_SECONDS`, `MAX_TOOL_CALLS`, `MAX_TOKENS_BUDGET`, `LLM_MAX_RETRIES`, `MEMORY_MAX_TOKENS`, `MEMORY_TARGET_TOKENS`, `MEMORY_KEEP_RECENT_TURNS`, `MEMORY_TTL_SECONDS`, `MEMORY_SWEEP_INTERVAL_SECONDS`, `TOOL_ALLOWLIST`, `TOOL_DENYLIST`.

### Skills (`agent/skills.py` + `skills/`)

Claude-Code-style progressive disclosure. Each skill is a folder with a `SKILL.md` (YAML frontmatter `name`/`description` + instruction body) plus optional bundled scripts. Only name+description go into the system prompt; the model calls the **skill's same-named tool** (no args) to load full instructions, then `run_skill_script` to run bundled scripts. `run_skill_script` enforces a path allowlist (scripts must resolve inside the skill dir) and a timeout. `{{CURRENT_DATE}}` in a skill body is substituted with the real date only when present (keeps the base prompt date-free).

Existing skills: `text-stats`, `web-research` (Tavily — **the only path to web search**; it is intentionally *not* a top-level tool, so the model can't bypass the skill), `ppt` (writes `.pptx` into `generated/`).

### Short-term memory (`memory/`)

`MemoryManager` keeps model context bounded independently from full MySQL chat
history. It retains a structured rolling summary plus recent complete turns,
compresses at configurable token watermarks, and falls back to deterministic
trimming if summarization fails. ReAct and plan-execute both consume this view;
the latter receives it through `conversation_context` and keeps `past_steps`
request-local. Successful summaries are persisted in `chat_summary` with their
covered `chat_message.seq` range; reopening a conversation loads that snapshot
plus only the uncovered text-message tail.

### Web API (`web_api/`, exposed by `server.py`)

`server.py` is the compatibility ASGI entrypoint; application assembly and business routers live in `web_api`. `web_api/runtime.py` owns the in-memory `sessions` cache (agent/mode/harness/memory/active_task). Idle sessions are evicted with a sliding TTL; full chat history remains in MySQL and rebuilds bounded memory when reopened. The chat router streams responses as SSE (`text/event-stream`). Event types: `text`, `thinking`, `tool`, `plan`/`step`/`step_*` (plan-execute), `limit` (budget/idle/cancel/recursion/content_filter), `error`, `done`. Notable behavior:
- **Idle timeout** caps the gap between two SSE events, not total duration — actively-streaming sessions never trip it.
- DashScope content-moderation rejections (`data_inspection_failed`) are surfaced as a clean `limit`/`content_filter` event, not a raw error.
- `/api/files/{filename}` serves generated artifacts from `generated/` with path-traversal guarding. `/api/title` best-effort-summarizes the first message into a sidebar title.

Builtin tools (`agent/tools.py`): `python_repl`, `get_current_time`, `get_current_location`, `get_weather`, `read_file`, `write_file`.

### Tests (`tests/`)

`conftest.py` sets dummy provider API keys **before** any agent import (constructors validate key presence), disables langsmith tracing, and clears `web_api.runtime.sessions` + forces GC after each test to keep RSS flat. Endpoint tests mirror the business modules under `tests/web_api/`; its local `conftest.py` provides the authenticated TestClient and mock agents. Tests mock the LLM rather than hitting providers.

## Conventions

- Logging goes through the top-level `logger` package (`get_logger`/`setup_logging`, idempotent); importing `logger` initializes logging automatically, and the rotating file handler writes to `logs/app.log`. Use `get_logger("<module>")`, not the stdlib root logger.
- When adding provider-specific code, remember the Qwen reasoning-content patch and the thinking-vs-structured-output exclusivity.
- `generated/` and `logs/` are gitignored runtime output dirs.
