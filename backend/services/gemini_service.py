import asyncio
import logging
import os
import time
from typing import Any, Optional

from google import genai as google_genai
from google.genai import types as genai_types

from backend.config.settings import get_settings
from backend.services.http_client import get_shared_http_client

logger = logging.getLogger(__name__)


class GeminiService:
    """Primary provider for Nezuko using Google Gemini."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        settings = get_settings()
        self.api_key = (
            api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("AI_API_KEY")
            or getattr(settings, "AI_API_KEY", None)
            or ""
        ).strip()
        self.model = (
            model
            or os.getenv("GEMINI_MODEL")
            or getattr(settings, "GEMINI_MODEL", None)
            or "gemini-3.5-flash-lite"
        ).strip()
        self._client: Optional[Any] = None

    def _get_client(self) -> Any:
        if self._client is None:
            if not self.api_key:
                raise RuntimeError("GEMINI_API_KEY is missing")
            self._client = google_genai.Client(api_key=self.api_key)
        return self._client

    async def generate(self, prompt: str, *, system_instruction: str, history: Optional[list[dict[str, Any]]] = None, timeout: float = 15.0) -> str:
        if not prompt:
            return ""

        client = self._get_client()
        contents = self._build_contents(prompt, history)
        client_http = get_shared_http_client()
        _ = client_http
        config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.7,
            topP=0.9,
            topK=50,
            maxOutputTokens=1024,
        )

        started_at = time.perf_counter()
        logger.info("[Gemini] Request started model=%s history_len=%d", self.model, len(history or []))

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self._invoke_generate_content, client, self.model, contents, config),
                timeout=timeout,
            )
            text = str(getattr(response, "text", "") or "").strip()
            logger.info("[Gemini] Success elapsed_ms=%d", int((time.perf_counter() - started_at) * 1000))
            return text
        except asyncio.TimeoutError as exc:
            logger.warning("[Gemini] Timeout after %.1fs", timeout)
            raise RuntimeError("timeout") from exc
        except Exception as exc:
            self._log_exception(exc)
            raise

    def _invoke_generate_content(self, client: Any, model: str, contents: list[Any], config: Any) -> Any:
        return client.models.generate_content(model=model, contents=contents, config=config)

    def _build_contents(self, prompt: str, history: Optional[list[dict[str, Any]]]) -> list[Any]:
        contents: list[Any] = []
        for item in history or []:
            role = str(item.get("role") or "user").strip().lower()
            if role == "assistant":
                role = "model"
            elif role == "system":
                continue
            elif role not in {"user", "model"}:
                role = "user"

            parts = item.get("parts") or item.get("content") or []
            if isinstance(parts, str):
                parts_list = [parts]
            elif isinstance(parts, list):
                parts_list = [str(part) for part in parts if str(part).strip()]
            else:
                parts_list = [str(parts)] if str(parts).strip() else []

            if parts_list:
                contents.append(genai_types.Content(role=role, parts=[genai_types.Part(text=str(part)) for part in parts_list]))

        contents.append(genai_types.Content(role="user", parts=[genai_types.Part(text=str(prompt))]))
        return contents

    def _log_exception(self, exc: Exception) -> None:
        message = str(exc).lower()
        if "429" in message or "rate limit" in message or "quota" in message or "resource_exhausted" in message:
            logger.warning("[Gemini] Rate Limited or Quota Exceeded")
        elif "5" in message and "0" in message:
            logger.warning("[Gemini] Temporary server error")
        else:
            logger.warning("[Gemini] Provider error: %s", exc)
