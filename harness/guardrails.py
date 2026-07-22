"""Unified harness guardrail orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .config import HarnessConfig
from .identity_guard import IdentityPrivacyGuardrail
from .safety_guard import SafetyGuardrail


@dataclass(frozen=True)
class GuardrailDecision:
    blocked: bool
    response: str | None
    reason: str


class InputGuardrail(Protocol):
    name: str
    system_rules: str

    def evaluate(self, text: str) -> GuardrailDecision: ...


_REGISTRY: dict[str, InputGuardrail] = {
    IdentityPrivacyGuardrail.name: IdentityPrivacyGuardrail(),
    SafetyGuardrail.name: SafetyGuardrail(),
}


def _enabled_guardrails(cfg: HarnessConfig) -> list[InputGuardrail]:
    return [guard for name in cfg.enabled_guardrails if (guard := _REGISTRY.get(name)) is not None]


def evaluate_input_guardrails(text: str, cfg: HarnessConfig) -> GuardrailDecision:
    for guard in _enabled_guardrails(cfg):
        decision = guard.evaluate(text)
        if decision.blocked:
            return decision
    return GuardrailDecision(blocked=False, response=None, reason="")


def guardrail_system_rules(cfg: HarnessConfig) -> str:
    rules: list[str] = []
    seen: set[str] = set()
    for guard in _enabled_guardrails(cfg):
        if guard.system_rules and guard.system_rules not in seen:
            rules.append(guard.system_rules)
            seen.add(guard.system_rules)
    return "\n\n".join(rules)
