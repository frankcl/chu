"""LLM retry helper."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from .config import HarnessConfig


def _retryable_exception_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = []
    try:
        import anthropic  # type: ignore
        types += [anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError]
    except Exception:
        pass
    try:
        import openai  # type: ignore
        types += [openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError]
    except Exception:
        pass
    try:
        import httpx  # type: ignore
        types += [httpx.TimeoutException, httpx.ConnectError]
    except Exception:
        pass
    return tuple(types) if types else (Exception,)


def apply_llm_retry(llm: BaseChatModel, cfg: HarnessConfig) -> BaseChatModel:
    """Wrap an LLM with exponential-backoff retry on transient provider errors."""
    if cfg.llm_max_retries <= 0:
        return llm
    exc_types = _retryable_exception_types()
    return llm.with_retry(
        retry_if_exception_type=exc_types,
        stop_after_attempt=cfg.llm_max_retries + 1,
        wait_exponential_jitter=True,
    )
