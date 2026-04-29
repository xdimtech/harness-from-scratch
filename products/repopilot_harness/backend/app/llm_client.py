from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

from anthropic import Anthropic
from dotenv import load_dotenv

DEFAULT_REQUEST_TIMEOUT_SECONDS = 90.0


@dataclass(slots=True)
class ToolUseRequest:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(slots=True)
class AssistantTurn:
    text: str
    tool_uses: list[ToolUseRequest]
    stop_reason: str | None
    content_blocks: list[dict[str, Any]]


class LLMClient(Protocol):
    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AssistantTurn: ...


class AnthropicLLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ):
        self.model = model
        self.base_url = base_url or ""
        self.timeout_seconds = max(10.0, float(timeout_seconds or DEFAULT_REQUEST_TIMEOUT_SECONDS))
        self.client = Anthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=self.timeout_seconds,
            max_retries=1,
        )

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AssistantTurn:
        response = self.client.messages.create(
            model=self.model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=0,
        )
        text_parts: list[str] = []
        tool_uses: list[ToolUseRequest] = []
        content_blocks: list[dict[str, Any]] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text = getattr(block, "text", "")
                if text:
                    text_parts.append(text)
                content_blocks.append({"type": "text", "text": text})
                continue
            if block_type == "tool_use":
                payload = dict(getattr(block, "input", {}) or {})
                tool_uses.append(ToolUseRequest(id=block.id, name=block.name, input=payload))
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": payload,
                    }
                )
                continue
            content_blocks.append({"type": str(block_type or "unknown")})
        return AssistantTurn(
            text="\n".join(part.strip() for part in text_parts if part.strip()).strip(),
            tool_uses=tool_uses,
            stop_reason=getattr(response, "stop_reason", None),
            content_blocks=content_blocks,
        )


def load_llm_env() -> dict[str, str]:
    # Prefer explicit process env over .env so tests and runtime overrides behave predictably.
    load_dotenv(override=False)
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
    return {
        "api_key": os.getenv("ANTHROPIC_API_KEY", "").strip(),
        "base_url": base_url,
        "model": os.getenv("MODEL_ID", "").strip(),
        "timeout_seconds": os.getenv("REPOPILOT_LLM_TIMEOUT_SECONDS", "").strip(),
    }


def llm_available() -> bool:
    config = load_llm_env()
    return bool(config["api_key"] and config["model"])


def llm_runtime_config() -> dict[str, Any]:
    config = load_llm_env()
    return {
        "available": bool(config["api_key"] and config["model"]),
        "model": config["model"],
        "base_url": config["base_url"],
        "uses_proxy": bool(config["base_url"]),
        "timeout_seconds": float(config["timeout_seconds"] or DEFAULT_REQUEST_TIMEOUT_SECONDS),
    }


def create_llm_client() -> AnthropicLLMClient:
    config = load_llm_env()
    if not config["api_key"]:
        raise ValueError("ANTHROPIC_API_KEY is required for direct_llm execution")
    if not config["model"]:
        raise ValueError("MODEL_ID is required for direct_llm execution")
    return AnthropicLLMClient(
        api_key=config["api_key"],
        base_url=config["base_url"] or None,
        model=config["model"],
        timeout_seconds=float(config["timeout_seconds"] or DEFAULT_REQUEST_TIMEOUT_SECONDS),
    )
