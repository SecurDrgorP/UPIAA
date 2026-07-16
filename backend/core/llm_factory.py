"""
LLM provider factory.

Nodes keep their UI schemas and validation, while provider-specific HTTP logic
lives here so future LLM integrations do not duplicate request/response code.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx


def _try_parse_json(text: str) -> Any:
    if not text or not isinstance(text, str):
        return text

    cleaned = text.strip()
    fence_match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    for pattern in (r"\{.*\}", r"\[.*\]"):
        match = re.search(pattern, cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                pass

    return text


class BaseLLMProvider(ABC):
    """Common interface for all text-generation providers."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate a model response."""


class OllamaProvider(BaseLLMProvider):
    def __init__(self, base_url: str, model: str, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, **kwargs},
        }
        if system:
            payload["system"] = system

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/api/generate", json=payload)
            if resp.status_code != 200:
                return {"response": "", "error": f"Ollama error {resp.status_code}: {resp.text}"}

            data = resp.json()
            return {
                "response": _try_parse_json(data.get("response") or ""),
                "model": self.model,
                "input_tokens": data.get("prompt_eval_count", 0),
                "output_tokens": data.get("eval_count", 0),
                "finish_reason": "stop" if data.get("done") else "unknown",
            }
        except Exception as e:
            return {"response": "", "error": str(e)}


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.api_key = api_key
        self.model = model

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> dict[str, Any]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
            if resp.status_code != 200:
                return {"response": "", "error": f"OpenAI error {resp.status_code}: {resp.text}"}

            data = resp.json()
            usage = data.get("usage", {})
            choice = data["choices"][0]
            response_text = choice["message"]["content"] or ""
            return {
                "response": _try_parse_json(response_text),
                "model": self.model,
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "finish_reason": choice.get("finish_reason", "unknown"),
            }
        except Exception as e:
            return {"response": "", "error": str(e)}


class GeminiProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str = "gemini-1.5-pro"):
        self.api_key = api_key
        self.model = model

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> dict[str, Any]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, **kwargs},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                return {"response": "", "error": f"Gemini error {resp.status_code}: {resp.text}"}

            data = resp.json()
            candidate = data["candidates"][0]
            usage = data.get("usageMetadata", {})
            response_text = candidate["content"]["parts"][0]["text"] or ""
            return {
                "response": _try_parse_json(response_text),
                "model": self.model,
                "input_tokens": usage.get("promptTokenCount", 0),
                "output_tokens": usage.get("candidatesTokenCount", 0),
                "finish_reason": candidate.get("finishReason", "unknown"),
            }
        except Exception as e:
            return {"response": "", "error": str(e)}


class OpenRouterProvider(OpenAIProvider):
    def __init__(self, api_key: str, model: str):
        super().__init__(api_key=api_key, model=model)

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> dict[str, Any]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://UPIAA.workflow.app",
            "X-Title": "UPIAA Workflow",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
            if resp.status_code != 200:
                return {"response": "", "error": f"OpenRouter error {resp.status_code}: {resp.text}"}

            data = resp.json()
            usage = data.get("usage", {})
            choice = data["choices"][0]
            response_text = choice["message"]["content"] or ""
            return {
                "response": _try_parse_json(response_text),
                "model": self.model,
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "finish_reason": choice.get("finish_reason", "unknown"),
            }
        except Exception as e:
            return {"response": "", "error": str(e)}


def get_llm_provider(name: str, config: dict[str, Any]) -> BaseLLMProvider:
    """Instantiate an LLM provider by provider name or node type."""
    key = (name or "").strip().lower()
    aliases = {
        "ollama_action": "ollama",
        "openai_action": "openai",
        "gemini_action": "gemini",
        "openrouter_action": "openrouter",
    }
    key = aliases.get(key, key)

    if key == "ollama":
        return OllamaProvider(
            base_url=config.get("ollama_url") or config.get("base_url") or "http://host.docker.internal:11434",
            model=config.get("model") or "llama3.1",
            timeout=int(config.get("timeout", 120)),
        )
    if key == "openai":
        return OpenAIProvider(
            api_key=config.get("api_key") or "",
            model=config.get("model") or "gpt-4o",
        )
    if key == "gemini":
        return GeminiProvider(
            api_key=config.get("api_key") or "",
            model=config.get("model") or "gemini-1.5-pro",
        )
    if key == "openrouter":
        return OpenRouterProvider(
            api_key=config.get("api_key") or "",
            model=config.get("model") or "meta-llama/llama-3-70b-instruct",
        )

    raise ValueError(f"Unsupported LLM provider: {name}")
