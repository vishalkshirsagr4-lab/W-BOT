import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from backend.services.ai_router import AIRouter


class AIRouterCostControlTests(unittest.TestCase):
    def test_openrouter_fallback_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {"AI_FALLBACK_ENABLED": "false"}, clear=False):
            router = AIRouter()

        router.gemini.generate = AsyncMock(side_effect=RuntimeError("HTTP 429 quota exceeded"))
        router.openrouter.generate = AsyncMock(return_value="paid fallback")

        reply = asyncio.run(
            router.generate("hello", system_instruction="test", history=[])
        )

        self.assertIn("temporarily busy", reply)
        router.openrouter.generate.assert_not_awaited()

    def test_openrouter_fallback_requires_explicit_opt_in(self) -> None:
        with patch.dict(os.environ, {"AI_FALLBACK_ENABLED": "true"}, clear=False):
            router = AIRouter()

        router.gemini.generate = AsyncMock(side_effect=RuntimeError("HTTP 429 quota exceeded"))
        router.openrouter.generate = AsyncMock(return_value="fallback reply")

        reply = asyncio.run(
            router.generate("hello", system_instruction="test", history=[])
        )

        self.assertEqual(reply, "fallback reply")
        router.openrouter.generate.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
