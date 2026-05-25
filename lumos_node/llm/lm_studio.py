from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import BaseModel

from ..config import get_settings


class ChatMessage(BaseModel):
    role: str
    # Content may be a plain string OR a list of OpenAI multimodal parts
    # (e.g. [{"type":"text","text":...},{"type":"image_url","image_url":{...}}]).
    content: str | list[dict[str, Any]] | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class CompletionChunk(BaseModel):
    delta: str = ""
    finished: bool = False
    usage: dict[str, Any] | None = None


class LMStudioClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 600.0,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.lm_studio_base_url).rstrip("/")
        self.api_key = api_key or settings.lm_studio_api_key
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_models(self) -> list[dict[str, Any]]:
        resp = await self._client.get(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        if not texts:
            return []
        resp = await self._client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": model, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        data.sort(key=lambda r: r.get("index", 0))
        return [r["embedding"] for r in data]

    async def speak(
        self,
        text: str,
        *,
        model: str,
        voice: str,
        response_format: str = "mp3",
        speed: float = 1.0,
    ) -> tuple[bytes, str]:
        """Synthesize speech via /v1/audio/speech. Returns (audio_bytes, mime_type)."""
        if not text.strip():
            return b"", _mime_for(response_format)
        payload: dict[str, Any] = {
            "model": model,
            "voice": voice,
            "input": text,
            "response_format": response_format,
            "speed": speed,
        }
        resp = await self._client.post(
            f"{self.base_url}/audio/speech",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", _mime_for(response_format))

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        chat_template_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Single non-streaming chat completion. Returns the raw message dict
        from LM Studio: {role, content, tool_calls?}.

        `response_format` accepts OpenAI structured-output schemas, e.g.
            {"type": "json_schema", "json_schema": {"name": "X", "schema": {...}}}
        LM Studio enforces the schema and guarantees valid JSON in `content`.

        `chat_template_kwargs` (Phase 33) — extra params forwarded to the model's
        Jinja chat template. Standard de-facto key across Qwen3.5 / Gemma 4 thinking
        models is `enable_thinking: bool`. Models that don't recognize a key ignore
        it harmlessly, so this is safe to pass even on non-thinking models.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format is not None:
            payload["response_format"] = response_format
        if chat_template_kwargs:
            payload["chat_template_kwargs"] = chat_template_kwargs
        resp = await self._client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]

    async def chat_stream(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        chat_template_kwargs: dict[str, Any] | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if chat_template_kwargs:
            payload["chat_template_kwargs"] = chat_template_kwargs
        async with self._client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        ) as resp:
            resp.raise_for_status()
            import json
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line.removeprefix("data: ").strip()
                if data == "[DONE]":
                    yield CompletionChunk(finished=True)
                    return
                obj = json.loads(data)
                usage = obj.get("usage")
                if usage:
                    yield CompletionChunk(usage=usage)
                choices = obj.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta", {}).get("content", "") or ""
                if delta:
                    yield CompletionChunk(delta=delta)
                # Note: do NOT yield finished=True on finish_reason; the usage
                # chunk arrives AFTER the finish_reason chunk per the OpenAI
                # spec. We rely on `[DONE]` as the sole terminal marker.


def _mime_for(fmt: str) -> str:
    return {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "opus": "audio/ogg",
        "flac": "audio/flac",
        "aac": "audio/aac",
        "pcm": "audio/pcm",
    }.get(fmt.lower(), "audio/mpeg")
