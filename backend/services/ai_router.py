import asyncio
import logging
import os
from typing import Any, Optional

from backend.services.gemini_service import GeminiService
from backend.services.openrouter_service import OpenRouterService

logger = logging.getLogger(__name__)


class AIRouter:
    """Use Gemini by default; only call OpenRouter when explicitly enabled."""

    def __init__(self) -> None:
        self.primary_provider = (os.getenv("PRIMARY_PROVIDER") or "gemini").strip().lower()
        self.fallback_enabled = os.getenv("AI_FALLBACK_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.gemini = GeminiService()
        self.openrouter = OpenRouterService()

    async def generate(self, prompt: str, *, system_instruction: str, history: Optional[list[dict[str, Any]]] = None) -> str:
        if not prompt:
            return ""

        if self.primary_provider == "openrouter":
            return await self._generate_with_openrouter(prompt, system_instruction=system_instruction, history=history)

        return await self._generate_with_primary_retry(prompt, system_instruction=system_instruction, history=history)

    async def _generate_with_primary_retry(self, prompt: str, *, system_instruction: str, history: Optional[list[dict[str, Any]]] = None) -> str:
        retries = [1, 2]
        for attempt in retries:
            try:
                logger.info("[Gemini] Attempt %d", attempt)
                return await self.gemini.generate(prompt, system_instruction=system_instruction, history=history, timeout=10.0)
            except Exception as exc:
                if not self._is_retryable_failure(exc):
                    logger.warning("[Gemini] Non-retryable failure: %s", exc)
                    break

                logger.warning("[Gemini] Retry %d after transient failure", attempt)
                if attempt == 2:
                    break
                await asyncio.sleep(0.5)
                continue

        if self.fallback_enabled:
            logger.warning("[Router] Gemini unavailable; OpenRouter fallback is explicitly enabled")
            return await self._generate_with_openrouter(prompt, system_instruction=system_instruction, history=history)

        logger.warning("[Router] Gemini unavailable; paid fallback disabled")
        return "🌸 Nezuko is temporarily busy. Please try again in a little while. 💖"

    async def _generate_with_openrouter(self, prompt: str, *, system_instruction: str, history: Optional[list[dict[str, Any]]] = None) -> str:
        try:
            response = await self.openrouter.generate(prompt, system_instruction=system_instruction, history=history, timeout=20.0)
            logger.info("[OpenRouter] Success")
            return response
        except Exception as exc:
            logger.warning("[OpenRouter] Failed: %s", exc)
            return "🌸 Nezuko is taking a tiny tea break, senpai! Please try again in a few moments. 💖"

    def _is_retryable_failure(self, exc: Exception) -> bool:
        message = str(exc).lower()
        if any(marker in message for marker in ["429", "rate limit", "quota", "resource_exhausted"]):
            return False

        if any(marker in message for marker in ["timeout", "temporarily", "server", "overloaded", "connection", "econnreset", "eai_again", "5xx", "503", "502", "500"]):
            return True

        return False
