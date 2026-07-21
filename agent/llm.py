"""第一层：通用大语言模型封装。

`LLM` 统一封装 Anthropic / OpenAI / Qwen 三家模型的调用：
provider 选择、thinking/reasoning 模式、Qwen 流式 reasoning_content 补丁，
以及对模型输出（thinking / text）的解析。
"""

import os

from langchain_core.language_models import BaseChatModel

from logger import get_logger

logger = get_logger("llm")


class _QwenChatOpenAI:
    """Mixin that rescues `reasoning_content` from Qwen streaming deltas.

    langchain-openai drops any delta key it doesn't know about.
    We override _convert_chunk_to_generation_chunk to pull
    `reasoning_content` out of the raw delta and inject it into
    AIMessageChunk.additional_kwargs before the chunk is returned.
    """

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        from langchain_core.messages import AIMessageChunk

        gen_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if gen_chunk is None:
            return gen_chunk
        choices = chunk.get("choices", [])
        if choices and isinstance(gen_chunk.message, AIMessageChunk):
            delta = choices[0].get("delta") or {}
            reasoning = delta.get("reasoning_content", "")
            if reasoning:
                gen_chunk.message.additional_kwargs["reasoning_content"] = reasoning
        return gen_chunk


class LLM:
    """统一封装大语言模型调用（Anthropic / OpenAI / Qwen）。

    provider 与模型名由环境变量 `LLM_PROVIDER` / `MODEL_NAME` 决定。
    thinking 模式可在构造时设定默认值，也可在 `chat_model()` 调用时覆盖。
    """

    def __init__(self, thinking: bool | None = None):
        # 记录默认 thinking 偏好；模型按需构建，不在构造时创建。
        self._thinking = thinking

    def chat_model(self, thinking: bool | None = None) -> BaseChatModel:
        """返回一个配置好的 LLM 实例。

        Args:
            thinking: 覆盖 thinking/reasoning 模式。
                None  → 使用构造时的默认值，再回退到环境变量 / 模型名启发式
                True  → 强制开启 thinking
                False → 强制关闭 thinking（结构化输出 / JSON 模式必须关闭，
                        否则 thinking 模型会返回空 content 导致解析失败）
        """
        if thinking is None:
            thinking = self._thinking
        provider = os.getenv("LLM_PROVIDER", "anthropic").lower()

        if provider == "openai":
            from langchain_openai import ChatOpenAI
            model_name = os.getenv("MODEL_NAME", "gpt-4o")
            logger.info("LLM init: provider=openai model=%s", model_name)
            # stream_usage=True：流式(astream)时也返回 token 用量，否则 usage_metadata 为空、
            # 无法统计消耗（发送 stream_options={"include_usage": true}）。
            return ChatOpenAI(model=model_name, stream_usage=True)

        if provider == "qwen":
            from langchain_openai import ChatOpenAI
            model_name = os.getenv("MODEL_NAME", "qwen-plus")
            # Determine thinking mode: explicit arg > env var > model-name heuristic.
            if thinking is True:
                thinking_on = True
            elif thinking is False:
                thinking_on = False
            else:
                thinking_env = os.getenv("ENABLE_THINKING", "").lower()
                if thinking_env in ("1", "true", "yes"):
                    thinking_on = True
                elif thinking_env in ("0", "false", "no"):
                    thinking_on = False
                else:
                    # qwen3-* models support thinking; older models don't.
                    thinking_on = model_name.lower().startswith("qwen3")
            extra_body = {"enable_thinking": True} if thinking_on else {}
            logger.info("LLM init: provider=qwen model=%s thinking=%s", model_name, thinking_on)

            # Build a subclass that rescues reasoning_content from streaming deltas.
            QwenChatOpenAI = type("QwenChatOpenAI", (_QwenChatOpenAI, ChatOpenAI), {})
            return QwenChatOpenAI(
                model=model_name,
                api_key=os.getenv("DASHSCOPE_API_KEY"),
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                extra_body=extra_body,
                # 流式时也返回 token 用量（DashScope 兼容模式支持 stream_options.include_usage），
                # 否则 usage_metadata 为空、消耗无法统计。
                stream_usage=True,
            )

        from langchain_anthropic import ChatAnthropic
        model_name = os.getenv("MODEL_NAME", "claude-sonnet-4-6")
        if thinking is True:
            enable_thinking = True
        elif thinking is False:
            enable_thinking = False
        else:
            enable_thinking = os.getenv("ENABLE_THINKING", "false").lower() in ("1", "true", "yes")
        thinking_cfg = {"type": "enabled", "budget_tokens": 8000} if enable_thinking else {"type": "disabled"}
        logger.info("LLM init: provider=anthropic model=%s thinking=%s", model_name, enable_thinking)
        return ChatAnthropic(
            model=model_name,
            thinking=thinking_cfg,
        )

    # ── 输出解析 ────────────────────────────────────────────────────────────

    @staticmethod
    def extract_text(content: str | list) -> str:
        if isinstance(content, list):
            return " ".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        return str(content)

    @staticmethod
    def iter_outputs(chunk):
        """Yield (kind, text) tuples for thinking/text content in an AIMessageChunk.

        kind ∈ {"thinking", "text"}. Covers:
          - Qwen reasoning_content (additional_kwargs)
          - Anthropic thinking / thinking_delta blocks (content as list)
          - regular text (string content, or list of {type:"text"})
        """
        reasoning = chunk.additional_kwargs.get("reasoning_content", "") if hasattr(chunk, "additional_kwargs") else ""
        if reasoning:
            yield ("thinking", reasoning)
        if isinstance(chunk.content, list):
            for block in chunk.content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") in ("thinking", "thinking_delta"):
                    t = block.get("thinking", "")
                    if t:
                        yield ("thinking", t)
        text = LLM.extract_text(chunk.content)
        if text:
            yield ("text", text)


# ── 向后兼容的模块级别名 ────────────────────────────────────────────────────

def get_llm(thinking: bool | None = None) -> BaseChatModel:
    """兼容旧 API：等价于 LLM().chat_model(thinking)。"""
    return LLM().chat_model(thinking)


extract_text_content = LLM.extract_text
iter_chunk_outputs = LLM.iter_outputs
