import argparse
import uuid

from dotenv import load_dotenv
from langchain_core.messages import AIMessageChunk
from langgraph.checkpoint.memory import MemorySaver

from agent import (
    ConsoleHitlChannel,
    create_agent,
    create_plan_execute_agent,
    extract_text_content,
)
from harness import BudgetTracker, HarnessConfig
from logger import get_logger

load_dotenv()
logger = get_logger("main")


def _stream_response(agent, user_input: str, config: dict):
    in_thinking = False
    response_started = False
    for chunk, metadata in agent.stream(
        {"messages": [("human", user_input)]},
        config=config,
        stream_mode="messages",
    ):
        if metadata.get("langgraph_node") != "agent":
            continue
        if not isinstance(chunk, AIMessageChunk):
            continue

        # Qwen3 / OpenAI-compatible: reasoning_content in additional_kwargs
        reasoning = chunk.additional_kwargs.get("reasoning_content", "")
        if reasoning:
            if not in_thinking:
                print("[thinking]", flush=True)
                in_thinking = True
            print(reasoning, end="", flush=True)

        # Anthropic extended thinking: thinking blocks in content list
        if isinstance(chunk.content, list):
            for block in chunk.content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                thinking_text = block.get("thinking", "") if btype in ("thinking", "thinking_delta") else ""
                if thinking_text:
                    if not in_thinking:
                        print("[thinking]", flush=True)
                        in_thinking = True
                    print(thinking_text, end="", flush=True)

        # Regular response text
        text = extract_text_content(chunk.content)
        if text:
            if in_thinking:
                print("\n[/thinking]\n", flush=True)
                in_thinking = False
            if not response_started:
                print("Agent: ", end="", flush=True)
                response_started = True
            print(text, end="", flush=True)

    if in_thinking:
        print("\n[/thinking]", flush=True)
    print()


def _run_plan_execute(agent, user_input: str, config: dict):
    for update in agent.stream(
        {"input": user_input, "plan": [], "past_steps": [], "response": None},
        config=config,
        stream_mode="updates",
    ):
        for node, data in update.items():
            if node == "plan":
                steps = data.get("plan", [])
                print("Plan:")
                for i, step in enumerate(steps, 1):
                    print(f"  {i}. {step}")
                print()
            elif node == "execute":
                for step, result in data.get("past_steps", []):
                    print(f"Step: {step}")
                    print(f"Result: {result}\n")
            elif node == "replan":
                if data.get("response") is not None:
                    print(f"Agent: {data['response']}")
                elif data.get("plan"):
                    print("Replanning:")
                    for i, step in enumerate(data["plan"], 1):
                        print(f"  {i}. {step}")
                    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["react", "plan-execute"],
        default="react",
        help="Agent mode (default: react)",
    )
    args = parser.parse_args()

    # Harness config drives the same runtime guards the server applies per turn:
    # recursion_limit, per-(skill,script) call cap, and the token / tool-call
    # budget. Without these the CLI has no backstop against a tool loop (e.g. the
    # ppt skill repeatedly calling build.py).
    cfg = HarnessConfig.from_env()

    def make_config(thread_id: str) -> dict:
        # Fresh per turn: a new BudgetTracker + skill_call_counts so counters
        # reset each request instead of accumulating across the CLI session.
        return {
            "configurable": {"thread_id": thread_id, "skill_call_counts": {}},
            "recursion_limit": cfg.recursion_limit,
            "callbacks": [BudgetTracker(cfg)],
        }

    # Terminal-backed HITL so request_user_choice (used by e.g. the ppt skill)
    # works in the CLI too — it prompts on stdin and blocks for the answer.
    hitl = ConsoleHitlChannel()
    if args.mode == "plan-execute":
        agent = create_plan_execute_agent(hitl_channel=hitl)
        run_fn = _run_plan_execute
        logger.info("CLI started mode=plan-execute")
        print("Plan-Execute Agent ready. Commands: 'quit' to exit.\n")
    else:
        agent = create_agent(checkpointer=MemorySaver(), hitl_channel=hitl)
        run_fn = _stream_response
        logger.info("CLI started mode=react")
        print("Agent ready. Commands: 'quit' to exit, 'new' to reset conversation.\n")

    thread_id = str(uuid.uuid4())

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            break
        if user_input.lower() == "new":
            thread_id = str(uuid.uuid4())
            print("(New conversation started)\n")
            continue

        try:
            run_fn(agent, user_input, make_config(thread_id))
        except Exception as e:
            logger.error("CLI error: %s", e, exc_info=True)
            print(f"\n[Error: {e}]")


if __name__ == "__main__":
    main()
