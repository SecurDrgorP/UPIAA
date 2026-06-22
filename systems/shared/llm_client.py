"""
Systems LLM Client — Resilient wrapper for Ollama /api/chat.

Exact copy of backend/ai/llm_client.py with ZERO backend imports.
All AI agents MUST use call_llm() or call_llm_raw() instead of direct httpx calls.
- Retry with exponential backoff
- Timeout handling
- Config from env vars
- Consistent /api/chat endpoint
"""

import asyncio
import json
import logging
import os
from typing import Any, Optional, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# ── Config from env ──────────────────────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
LLM_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))
LLM_MAX_RETRIES = int(os.getenv("OLLAMA_MAX_RETRIES", "3"))


async def _chat_completion(
    prompt: str,
    system_prompt: str = "",
    model: Optional[str] = None,
    format_json: bool = False,
) -> str:
    """
    Low-level call to Ollama /api/chat with retry + timeout.
    Returns the full response text.
    """
    model = model or OLLAMA_MODEL
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if format_json:
        payload["format"] = "json"

    last_error: Exception | None = None

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
                resp = await client.post(
                    f"{OLLAMA_URL}/api/chat",
                    json=payload,
                )
                if resp.status_code != 200:
                    raise httpx.HTTPStatusError(
                        f"Ollama returned {resp.status_code}: {resp.text}",
                        request=resp.request,
                        response=resp,
                    )

                # Stream and accumulate response
                content = ""
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        content += chunk.get("message", {}).get("content", "")
                        if chunk.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue

                return content.strip()

        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
            last_error = e
            if attempt < LLM_MAX_RETRIES:
                wait = 2 ** attempt
                logger.warning(
                    f"LLM call attempt {attempt}/{LLM_MAX_RETRIES} failed: {e}. "
                    f"Retrying in {wait}s..."
                )
                await asyncio.sleep(wait)
            else:
                logger.error(f"LLM call failed after {LLM_MAX_RETRIES} attempts: {e}")

    raise RuntimeError(
        f"LLM call failed after {LLM_MAX_RETRIES} attempts: {last_error}"
    )


async def call_llm_raw(
    prompt: str,
    system_prompt: str = "",
    model: Optional[str] = None,
) -> str:
    """
    Call the LLM and return the raw text response.
    Use this when you don't need structured output.
    """
    return await _chat_completion(
        prompt=prompt,
        system_prompt=system_prompt,
        model=model,
        format_json=False,
    )


async def call_llm(
    prompt: str,
    system_prompt: str = "",
    model: Optional[str] = None,
    response_model: Optional[Type[T]] = None,
) -> T | dict:
    """
    Call the LLM and parse the response into a Pydantic model.
    If response_model is provided, validates and returns an instance of it.
    Otherwise returns the parsed JSON dict.
    """
    raw = await _chat_completion(
        prompt=prompt,
        system_prompt=system_prompt,
        model=model,
        format_json=True,
    )

    # Try to extract JSON from code fences if present
    cleaned = raw
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1]
        if "```" in cleaned:
            cleaned = cleaned.split("```", 1)[0]
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1]
        if "```" in cleaned:
            cleaned = cleaned.split("```", 1)[0]

    try:
        parsed = json.loads(cleaned.strip())
    except json.JSONDecodeError as e:
        logger.error(f"LLM returned invalid JSON: {e}\nRaw response:\n{raw[:500]}")
        raise ValueError(f"LLM returned invalid JSON: {e}")

    if response_model is not None:
        try:
            return response_model.model_validate(parsed)
        except ValidationError as e:
            logger.error(f"LLM response failed Pydantic validation: {e}")
            raise ValueError(f"LLM response failed schema validation: {e}")

    return parsed
